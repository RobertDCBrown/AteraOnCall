from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, time, timedelta
import os
from dotenv import load_dotenv
import requests
import json
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///oncall.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Add context processor for templates
@app.context_processor
def inject_now():
    return {
        'now': datetime.now(),
        'format_datetime': format_datetime,
        'format_date': lambda date: date.strftime('%Y-%m-%d') if date else ''
    }

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(100), nullable=False)

class OnCallSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    technician = db.relationship('Technician', backref='schedules')

class BusinessHours(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0=Monday, 6=Sunday
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String(50), nullable=False, unique=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False)
    priority = db.Column(db.String(20))
    status = db.Column(db.String(20))
    client = db.Column(db.String(100))
    user = db.Column(db.String(100))
    notified = db.Column(db.Boolean, default=False)
    
class SystemSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), nullable=False, unique=True)
    value = db.Column(db.String(255))

class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text, nullable=True)
    notified = db.Column(db.Boolean, default=False)

# Helper functions
def get_setting(key, default=''):
    """Get a setting value from the database or return the default"""
    try:
        with app.app_context():
            setting = SystemSetting.query.filter_by(key=key).first()
            if setting:
                return setting.value
            return default
    except Exception as e:
        app.logger.warning(f"Error getting setting {key}: {str(e)}")
        return default

def save_setting(key, value):
    """Save a setting value to the database"""
    try:
        with app.app_context():
            setting = SystemSetting.query.filter_by(key=key).first()
            if setting:
                setting.value = value
            else:
                setting = SystemSetting(key=key, value=value)
                db.session.add(setting)
            db.session.commit()
            return True
    except Exception as e:
        app.logger.error(f"Error saving setting {key}: {str(e)}")
        db.session.rollback()
        return False

def get_timezone():
    """Get the configured timezone or default to UTC"""
    with app.app_context():
        setting = SystemSetting.query.filter_by(key='timezone').first()
        if setting and setting.value in pytz.all_timezones:
            return pytz.timezone(setting.value)
        return pytz.timezone('UTC')  # Default to UTC

def convert_to_local_time(dt):
    """Convert a datetime to the configured local timezone"""
    if dt is None:
        return None
        
    # If the datetime is naive (no timezone info), assume it's UTC
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
        
    # Convert to the configured timezone
    local_tz = get_timezone()
    return dt.astimezone(local_tz)

def format_datetime(dt, format_str='%Y-%m-%d %H:%M'):
    """Format a datetime in the configured timezone"""
    if dt is None:
        return ''
        
    local_dt = convert_to_local_time(dt)
    return local_dt.strftime(format_str)

def is_business_hours(dt=None):
    """Check if the current time is within business hours and not a holiday"""
    if dt is None:
        dt = datetime.now()
    
    # Ensure the datetime is in the local timezone
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        # Convert to local timezone if it has timezone info
        local_dt = convert_to_local_time(dt)
    else:
        # If it's a naive datetime, assume it's already local
        local_dt = dt
    
    app.logger.debug(f"Checking business hours for datetime: {local_dt} (original: {dt})")
    
    # Check if today is a holiday
    today_date = local_dt.date()
    holiday = Holiday.query.filter_by(date=today_date).first()
    if holiday:
        app.logger.info(f"Today is a holiday: {holiday.name}")
        return False  # If it's a holiday, it's not business hours
    
    # Get day of week (0 = Monday, 6 = Sunday)
    day_of_week = local_dt.weekday()
    app.logger.debug(f"Day of week: {day_of_week} (0=Monday, 6=Sunday)")
    
    # Get current time
    current_time = local_dt.time()
    app.logger.debug(f"Current time: {current_time}")
    
    # Check if business hours exist for this day
    business_hours = BusinessHours.query.filter_by(day_of_week=day_of_week).first()
    if not business_hours:
        app.logger.info(f"No business hours defined for day {day_of_week}")
        return False
    
    app.logger.debug(f"Business hours for day {day_of_week}: {business_hours.start_time} - {business_hours.end_time}")
    
    is_within_hours = business_hours.start_time <= current_time <= business_hours.end_time
    app.logger.info(f"Is within business hours: {is_within_hours}")
    
    return is_within_hours

def get_current_on_call():
    """Get all current on-call technicians (handles overlapping schedules)"""
    now = datetime.now()
    schedules = OnCallSchedule.query.filter(
        OnCallSchedule.start_date <= now,
        OnCallSchedule.end_date >= now
    ).all()
    
    technicians = []
    for schedule in schedules:
        if schedule.technician not in technicians:
            technicians.append(schedule.technician)
    
    return technicians

def send_sms_notification(technician, ticket, holiday_message=""):
    """Send SMS notification to on-call technician"""
    # Check if the ticket has already been notified
    if ticket.notified:
        app.logger.info(f"Skipping notification for ticket #{ticket.ticket_id} as it was already sent")
        return True
        
    # Get Twilio credentials from settings (fall back to environment variables if not in database)
    account_sid = get_setting('twilio_account_sid', os.getenv('TWILIO_ACCOUNT_SID', ''))
    auth_token = get_setting('twilio_auth_token', os.getenv('TWILIO_AUTH_TOKEN', ''))
    from_number = get_setting('twilio_phone_number', os.getenv('TWILIO_PHONE_NUMBER', ''))
    
    if not all([account_sid, auth_token, from_number]):
        app.logger.error("Twilio credentials not configured")
        return False
    
    # Check if technician has a valid phone number
    if not technician.phone or not technician.phone.strip():
        app.logger.error(f"Cannot send notification: Technician {technician.name} has no phone number")
        return False
        
    client = Client(account_sid, auth_token)
    
    # Format the message according to the requested template
    message = f"New On Call Ticket{holiday_message}\n"
    message += f"Client: {ticket.client if hasattr(ticket, 'client') and ticket.client else 'Unknown'}\n"
    message += f"User: {ticket.user if hasattr(ticket, 'user') and ticket.user else 'Unknown'}\n"
    message += f"Subject: {ticket.title}\n"
    message += f"Link: https://app.atera.com/new/ticket/{ticket.ticket_id}"
    
    try:
        # Log the notification attempt
        app.logger.info(f"Initiating Twilio SMS to {technician.name} at {technician.phone} for ticket #{ticket.ticket_id}")
        
        # Send the message
        try:
            message_response = client.messages.create(
                body=message,
                from_=from_number,
                to=technician.phone
            )
            
            # Log the successful message with Twilio SID for tracking
            app.logger.info(f"SMS notification sent to {technician.name} for ticket #{ticket.ticket_id} - Twilio SID: {message_response.sid}")
            return True
            
        except TwilioRestException as e:
            # Handle specific Twilio errors with detailed logging
            error_code = e.code if hasattr(e, 'code') else 'unknown'
            error_msg = e.msg if hasattr(e, 'msg') else str(e)
            
            app.logger.error(f"Twilio API error when sending SMS to {technician.name}: Code {error_code} - {error_msg}")
            
            # Log specific error types for easier troubleshooting
            if error_code == 21211:
                app.logger.error(f"Invalid phone number format for {technician.name}: {technician.phone}")
            elif error_code == 21612:
                app.logger.error("Twilio account lacks permission to send SMS to this number")
            elif error_code == 21608:
                app.logger.error("Twilio message body exceeds maximum allowed length")
            elif error_code == 20003:
                app.logger.error("Twilio authentication error - check account SID and auth token")
            
            return False
            
        except Exception as e:
            app.logger.error(f"Unexpected error when sending SMS via Twilio: {str(e)}")
            return False
            
    except Exception as e:
        app.logger.error(f"Failed to send SMS (general error): {str(e)}")
        return False

def fetch_tickets_from_atera():
    """Fetch tickets from Atera API"""
    # Update the last check time
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    last_check = SystemSetting.query.filter_by(key='last_ticket_check').first()
    
    if last_check:
        last_check.value = current_time
    else:
        last_check = SystemSetting(key='last_ticket_check', value=current_time)
        db.session.add(last_check)
    
    db.session.commit()
    
    # Get Atera API key from settings (fall back to environment variable if not in database)
    api_key = get_setting('atera_api_key', os.getenv('ATERA_API_KEY', ''))
    if not api_key:
        app.logger.error("Atera API key not configured")
        return []
    
    headers = {
        'X-API-KEY': api_key,
        'Accept': 'application/json'
    }
    
    try:
        app.logger.info("Initiating connection to Atera API to fetch tickets")
        
        # Fetch tickets from Atera API with query parameters for open tickets
        try:
            response = requests.get(
                'https://app.atera.com/api/v3/tickets',
                headers=headers,
                params={
                    'page': 1,
                    'itemsInPage': 50,
                    'ticketStatus': 'Open'
                },
                timeout=30  # Add timeout to prevent hanging indefinitely
            )
            
            app.logger.info(f"Atera API response received with status code: {response.status_code}")
            
            if response.status_code != 200:
                error_message = f"Failed to fetch tickets from Atera API: HTTP {response.status_code}"
                try:
                    error_detail = response.json()
                    error_message += f" - {error_detail}"
                except:
                    pass
                
                app.logger.error(error_message)
                return []
                
        except requests.exceptions.Timeout:
            app.logger.error("Timeout error connecting to Atera API - connection timed out after 30 seconds")
            return []
        except requests.exceptions.ConnectionError as e:
            app.logger.error(f"Connection error connecting to Atera API: {str(e)}")
            return []
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Error connecting to Atera API: {str(e)}")
            return []
        
        # Get the items from the response
        response_data = response.json()
        tickets = response_data.get('items', [])
        
        app.logger.info(f"Found {len(tickets)} tickets from Atera API")
        
        # Process new tickets
        for ticket_data in tickets:
            try:
                # Extract ticket ID
                ticket_id = str(ticket_data.get('TicketID'))
                app.logger.debug(f"Processing ticket ID: {ticket_id}")
                
                # Check if ticket already exists in our database
                existing_ticket = Ticket.query.filter_by(ticket_id=ticket_id).first()
                if existing_ticket:
                    app.logger.debug(f"Skipping existing ticket: {ticket_id}")
                    continue
                
                app.logger.info(f"Found new ticket: {ticket_id}")
                    
                # Get ticket details
                title = ticket_data.get('TicketTitle', 'No Title')
                description = ticket_data.get('FirstComment', '')
                status = ticket_data.get('TicketStatus', 'Unknown')
                priority = ticket_data.get('TicketPriority', 'Unknown')
                
                # Extract client and user information
                client = ticket_data.get('CustomerName', 'Unknown')
                
                # Combine first and last name for the user if available
                first_name = ticket_data.get('EndUserFirstName', '')
                last_name = ticket_data.get('EndUserLastName', '')
                if first_name or last_name:
                    user = f"{first_name} {last_name}".strip()
                else:
                    user = 'Unknown'
                
                # Parse creation date - use current time if not available
                try:
                    # The API returns date in format: 2025-04-16T16:34:41Z
                    created_at_str = ticket_data.get('TicketCreatedDate')
                    if created_at_str:
                        # Remove the Z and handle timezone
                        created_at_str = created_at_str.replace('Z', '+00:00')
                        created_at = datetime.fromisoformat(created_at_str)
                        app.logger.debug(f"Ticket {ticket_id} created at UTC: {created_at}")
                        
                        # Convert to local timezone for proper business hours checking
                        local_created_at = convert_to_local_time(created_at)
                        app.logger.debug(f"Ticket {ticket_id} created at local time: {local_created_at}")
                    else:
                        app.logger.warning(f"No creation date found for ticket {ticket_id}, using current time")
                        created_at = datetime.now()
                        local_created_at = created_at  # Already local time
                except (ValueError, TypeError) as e:
                    app.logger.error(f"Error parsing date for ticket {ticket_id}: {str(e)} - Date string: {created_at_str if 'created_at_str' in locals() else 'N/A'}")
                    created_at = datetime.now()
                    local_created_at = created_at  # Already local time
                    app.logger.warning(f"Using current time for ticket {ticket_id} creation date")
                
                # Create new ticket record
                try:
                    new_ticket = Ticket(
                        ticket_id=ticket_id,
                        title=title,
                        description=description,
                        status=status,
                        priority=priority,
                        client=client,
                        user=user,
                        created_at=created_at,
                        notified=False
                    )
                    
                    # Add to database session
                    db.session.add(new_ticket)
                    db.session.flush()  # Flush to get the ID without committing
                    app.logger.debug(f"Successfully added ticket {ticket_id} to database session")
                except Exception as e:
                    app.logger.error(f"Error creating ticket record in database: {str(e)}")
                    continue  # Skip to next ticket if we can't create this one
                
                # Check if today is a holiday
                today_date = datetime.now().date()
                holiday = Holiday.query.filter_by(date=today_date).first()
                
                app.logger.info(f"Checking notification criteria for ticket {ticket_id}")
                app.logger.info(f"Ticket creation time (UTC): {created_at}")
                app.logger.info(f"Ticket creation time (local): {local_created_at}")
                
                # Check business hours status using the local creation time
                is_within_hours = is_business_hours(local_created_at)
                app.logger.info(f"Is ticket within business hours: {is_within_hours}")
                
                # Check if notification is needed (outside business hours or holiday)
                should_notify = holiday or not is_within_hours
                app.logger.info(f"Should send notification: {should_notify} (Holiday: {bool(holiday)}, Outside business hours: {not is_within_hours})")
                
                if should_notify:
                    # Get all current on-call technicians
                    technicians = get_current_on_call()
                    app.logger.info(f"Found {len(technicians)} on-call technicians")
                    
                    if technicians:
                        notification_sent = False
                        
                        # Prepare notification message with holiday info if applicable
                        holiday_message = f" (Holiday: {holiday.name})" if holiday else ""
                        
                        # Send notification to each on-call technician
                        for technician in technicians:
                            app.logger.info(f"Sending notification for ticket {ticket_id} to {technician.name}{holiday_message}")
                            
                            # Send notification
                            if send_sms_notification(technician, new_ticket, holiday_message):
                                notification_sent = True
                                app.logger.info(f"Notification sent successfully to {technician.name} for ticket {ticket_id}")
                            else:
                                app.logger.error(f"Failed to send notification to {technician.name} for ticket {ticket_id}")
                        
                        # Mark as notified if at least one notification was sent successfully
                        if notification_sent:
                            new_ticket.notified = True
                            
                            # Mark holiday as notified if applicable
                            if holiday and not holiday.notified:
                                holiday.notified = True
                                app.logger.info(f"Holiday {holiday.name} marked as notified")
                            
                app.logger.info(f"Added new ticket: {ticket_id} - {title}")
                            
            except Exception as e:
                app.logger.error(f"Error processing ticket: {str(e)}")
                continue
        
        try:
            db.session.commit()
            app.logger.info(f"Successfully committed {len(tickets)} new tickets to database")
            return tickets
        except Exception as e:
            app.logger.error(f"Database commit error: {str(e)}")
            db.session.rollback()
            app.logger.warning("Database changes rolled back due to error")
            return []
    except Exception as e:
        app.logger.error(f"Error fetching tickets: {str(e)}")
        try:
            db.session.rollback()
            app.logger.warning("Database changes rolled back due to error")
        except Exception as rollback_error:
            app.logger.critical(f"Failed to rollback database transaction: {str(rollback_error)}")
        return []

# Get refresh interval from settings or use default (5 minutes)
def get_refresh_interval():
    try:
        with app.app_context():
            setting = SystemSetting.query.filter_by(key='refresh_interval').first()
            if setting and setting.value.isdigit():
                return int(setting.value)
            return 5  # Default: 5 minutes
    except Exception as e:
        app.logger.warning(f"Error getting refresh interval: {str(e)}. Using default value.")
        return 5  # Default: 5 minutes

# Schedule the ticket fetching job with the configurable interval
@scheduler.scheduled_job('interval', minutes=get_refresh_interval())
def scheduled_ticket_check():
    job_start_time = datetime.now()
    app.logger.info(f"Starting scheduled ticket check at {job_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        with app.app_context():
            app.logger.info(f"Running scheduled ticket check (every {get_refresh_interval()} minutes)")
            
            # Update last check time
            try:
                now = datetime.now()
                last_check = SystemSetting.query.filter_by(key='last_ticket_check').first()
                if last_check:
                    last_check.value = format_datetime(now)
                    app.logger.debug(f"Updated last_ticket_check to {format_datetime(now)}")
                else:
                    last_check = SystemSetting(key='last_ticket_check', value=format_datetime(now))
                    db.session.add(last_check)
                    app.logger.debug(f"Created new last_ticket_check with value {format_datetime(now)}")
                db.session.commit()
            except Exception as e:
                app.logger.error(f"Database error updating last check time: {str(e)}")
                try:
                    db.session.rollback()
                    app.logger.warning("Database changes rolled back due to error")
                except Exception as rollback_error:
                    app.logger.critical(f"Failed to rollback database transaction: {str(rollback_error)}")
            
            # Fetch tickets
            try:
                tickets = fetch_tickets_from_atera()
                app.logger.info(f"Ticket check completed, processed {len(tickets) if tickets else 0} tickets")
            except Exception as e:
                app.logger.error(f"Error fetching tickets from Atera: {str(e)}")
                
    except Exception as e:
        app.logger.error(f"Unhandled error in scheduled ticket check: {str(e)}")
    finally:
        job_end_time = datetime.now()
        duration = (job_end_time - job_start_time).total_seconds()
        app.logger.info(f"Scheduled ticket check completed in {duration:.2f} seconds")

# Routes
@app.route('/restart-service')
@login_required
def restart_service():
    """Restart the application service"""
    try:
        app.logger.info(f"Service restart initiated by {current_user.username}")
        
        # Return success response before actually restarting
        response = jsonify({
            'success': True,
            'message': 'Service restart initiated. Please wait a moment for the service to restart.'
        })
        
        # Schedule the restart to happen after the response is sent
        def restart_after_response():
            # Give a brief delay to ensure the response is sent
            import time
            time.sleep(1)
            
            # Attempt to restart using different methods depending on the environment
            import os
            import sys
            import subprocess
            
            app.logger.info("Executing service restart")
            
            try:
                # Check if we're running as a Windows service
                service_name = os.getenv('SERVICE_NAME', 'OnCallTicketMonitor')
                subprocess.run(['sc', 'stop', service_name], check=False)
                subprocess.run(['sc', 'start', service_name], check=False)
                app.logger.info(f"Attempted to restart Windows service: {service_name}")
            except Exception as e:
                app.logger.error(f"Failed to restart as Windows service: {str(e)}")
                
                # Fallback to restarting the Python process
                try:
                    os.execv(sys.executable, ['python'] + sys.argv)
                    app.logger.info("Attempted to restart Python process")
                except Exception as e:
                    app.logger.critical(f"Failed to restart Python process: {str(e)}")
        
        # Start the restart process in a separate thread
        import threading
        restart_thread = threading.Thread(target=restart_after_response)
        restart_thread.daemon = True
        restart_thread.start()
        
        return response
        
    except Exception as e:
        app.logger.error(f"Error initiating service restart: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error restarting service: {str(e)}'
        }), 500

@app.route('/refresh-tickets')
@login_required
def refresh_tickets_route():
    """Manually refresh tickets from Atera API"""
    job_start_time = datetime.now()
    app.logger.info(f"Manual ticket refresh initiated by {current_user.username} at {job_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Update last check time
        now = datetime.now()
        try:
            last_check = SystemSetting.query.filter_by(key='last_ticket_check').first()
            if last_check:
                last_check.value = format_datetime(now)
                app.logger.debug(f"Updated last_ticket_check to {format_datetime(now)}")
            else:
                last_check = SystemSetting(key='last_ticket_check', value=format_datetime(now))
                db.session.add(last_check)
                app.logger.debug(f"Created new last_ticket_check with value {format_datetime(now)}")
            db.session.commit()
        except Exception as e:
            app.logger.error(f"Database error updating last check time: {str(e)}")
            try:
                db.session.rollback()
                app.logger.warning("Database changes rolled back due to error")
            except Exception as rollback_error:
                app.logger.critical(f"Failed to rollback database transaction: {str(rollback_error)}")
            return jsonify({
                'success': False,
                'message': f'Database error: {str(e)}'
            }), 500
        
        # Fetch tickets
        try:
            tickets = fetch_tickets_from_atera()
            
            job_end_time = datetime.now()
            duration = (job_end_time - job_start_time).total_seconds()
            app.logger.info(f"Manual ticket refresh completed in {duration:.2f} seconds, processed {len(tickets) if tickets else 0} tickets")
            
            # Return JSON response for AJAX requests
            return jsonify({
                'success': True,
                'message': f'Successfully fetched {len(tickets)} tickets in {duration:.2f} seconds',
                'last_check': format_datetime(now),
                'ticket_count': len(tickets)
            })
        except Exception as e:
            app.logger.error(f"Error fetching tickets from Atera API: {str(e)}")
            return jsonify({
                'success': False,
                'message': f'API Error: {str(e)}'
            }), 503  # Service Unavailable
            
    except Exception as e:
        app.logger.error(f"Unhandled error refreshing tickets: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error refreshing tickets: {str(e)}'
        }), 500

@app.route('/')
@login_required
def index():
    tickets = Ticket.query.order_by(Ticket.created_at.desc()).limit(20).all()
    current_on_call_technicians = get_current_on_call()
    
    # Get the last ticket check time
    last_check = SystemSetting.query.filter_by(key='last_ticket_check').first()
    last_check_time = last_check.value if last_check else None
    
    return render_template('index.html', 
                           tickets=tickets, 
                           current_on_call_technicians=current_on_call_technicians,
                           last_check_time=last_check_time)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.password == password:  # In production, use proper password hashing
            login_user(user)
            return redirect(url_for('index'))
        
        flash('Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/technicians')
@login_required
def technicians():
    techs = Technician.query.all()
    return render_template('technicians.html', technicians=techs)

@app.route('/technicians/add', methods=['GET', 'POST'])
@login_required
def add_technician():
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')
        
        new_tech = Technician(name=name, phone=phone, email=email)
        db.session.add(new_tech)
        db.session.commit()
        
        flash('Technician added successfully')
        return redirect(url_for('technicians'))
    
    return render_template('add_technician.html')

@app.route('/technicians/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_technician(id):
    tech = Technician.query.get_or_404(id)
    
    if request.method == 'POST':
        tech.name = request.form.get('name')
        tech.phone = request.form.get('phone')
        tech.email = request.form.get('email')
        
        db.session.commit()
        
        flash('Technician updated successfully')
        return redirect(url_for('technicians'))
    
    return render_template('edit_technician.html', technician=tech)

@app.route('/technicians/delete/<int:id>')
@login_required
def delete_technician(id):
    tech = Technician.query.get_or_404(id)
    db.session.delete(tech)
    db.session.commit()
    
    flash('Technician deleted successfully')
    return redirect(url_for('technicians'))

@app.route('/oncall')
@login_required
def oncall():
    schedules = OnCallSchedule.query.order_by(OnCallSchedule.start_date).all()
    technicians = Technician.query.all()
    return render_template('oncall.html', schedules=schedules, technicians=technicians)

@app.route('/oncall/add', methods=['GET', 'POST'])
@login_required
def add_oncall():
    if request.method == 'POST':
        technician_id = request.form.get('technician_id')
        start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%dT%H:%M')
        end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%dT%H:%M')
        
        new_schedule = OnCallSchedule(
            technician_id=technician_id,
            start_date=start_date,
            end_date=end_date
        )
        
        db.session.add(new_schedule)
        db.session.commit()
        
        flash('On-call schedule added successfully')
        return redirect(url_for('oncall'))
    
    technicians = Technician.query.all()
    return render_template('add_oncall.html', technicians=technicians)

@app.route('/oncall/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_oncall(id):
    schedule = OnCallSchedule.query.get_or_404(id)
    
    if request.method == 'POST':
        schedule.technician_id = request.form.get('technician_id')
        schedule.start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%dT%H:%M')
        schedule.end_date = datetime.strptime(request.form.get('end_date'), '%Y-%m-%dT%H:%M')
        
        db.session.commit()
        
        flash('On-call schedule updated successfully')
        return redirect(url_for('oncall'))
    
    technicians = Technician.query.all()
    return render_template('edit_oncall.html', schedule=schedule, technicians=technicians)

@app.route('/oncall/delete/<int:id>')
@login_required
def delete_oncall(id):
    schedule = OnCallSchedule.query.get_or_404(id)
    db.session.delete(schedule)
    db.session.commit()
    
    flash('On-call schedule deleted successfully')
    return redirect(url_for('oncall'))

@app.route('/business-hours')
@login_required
def business_hours():
    hours = BusinessHours.query.order_by(BusinessHours.day_of_week).all()
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    return render_template('business_hours.html', hours=hours, days=days)

@app.route('/holidays')
@login_required
def holidays():
    # Get all holidays sorted by date
    all_holidays = Holiday.query.order_by(Holiday.date).all()
    return render_template('holidays.html', holidays=all_holidays)

@app.route('/holidays/add', methods=['POST'])
@login_required
def add_holiday():
    if request.method == 'POST':
        name = request.form.get('name')
        date_str = request.form.get('date')
        description = request.form.get('description', '')
        
        try:
            # Parse the date string into a date object
            holiday_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Create new holiday
            holiday = Holiday(
                name=name,
                date=holiday_date,
                description=description,
                notified=False
            )
            
            db.session.add(holiday)
            db.session.commit()
            
            flash(f'Holiday "{name}" added successfully', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Error adding holiday: {str(e)}")
            flash(f'Error adding holiday: {str(e)}', 'danger')
        
    return redirect(url_for('holidays'))

@app.route('/holidays/edit/<int:holiday_id>', methods=['GET', 'POST'])
@login_required
def edit_holiday(holiday_id):
    holiday = Holiday.query.get_or_404(holiday_id)
    
    if request.method == 'POST':
        name = request.form.get('name')
        date_str = request.form.get('date')
        description = request.form.get('description', '')
        
        try:
            # Parse the date string into a date object
            holiday_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Update holiday
            holiday.name = name
            holiday.date = holiday_date
            holiday.description = description
            holiday.notified = False  # Reset notification status when edited
            
            db.session.commit()
            
            flash(f'Holiday "{name}" updated successfully', 'success')
            return redirect(url_for('holidays'))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Error updating holiday: {str(e)}")
            flash(f'Error updating holiday: {str(e)}', 'danger')
    
    return render_template('edit_holiday.html', holiday=holiday)

@app.route('/holidays/delete/<int:holiday_id>')
@login_required
def delete_holiday(holiday_id):
    holiday = Holiday.query.get_or_404(holiday_id)
    
    try:
        name = holiday.name
        db.session.delete(holiday)
        db.session.commit()
        flash(f'Holiday "{name}" deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error deleting holiday: {str(e)}")
        flash(f'Error deleting holiday: {str(e)}', 'danger')
    
    return redirect(url_for('holidays'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Settings page for configuring application settings"""
    # Get current refresh interval
    refresh_setting = SystemSetting.query.filter_by(key='refresh_interval').first()
    current_refresh = int(refresh_setting.value) if refresh_setting and refresh_setting.value.isdigit() else 5
    
    # Get current timezone
    timezone_setting = SystemSetting.query.filter_by(key='timezone').first()
    current_timezone = timezone_setting.value if timezone_setting else 'UTC'
    
    # Get current API keys
    atera_api_key = get_setting('atera_api_key', os.getenv('ATERA_API_KEY', ''))
    twilio_account_sid = get_setting('twilio_account_sid', os.getenv('TWILIO_ACCOUNT_SID', ''))
    twilio_auth_token = get_setting('twilio_auth_token', os.getenv('TWILIO_AUTH_TOKEN', ''))
    twilio_phone_number = get_setting('twilio_phone_number', os.getenv('TWILIO_PHONE_NUMBER', ''))
    
    if request.method == 'POST':
        # Get form values
        new_refresh = request.form.get('refresh_interval', '5')
        new_timezone = request.form.get('timezone', 'UTC')
        new_atera_api_key = request.form.get('atera_api_key', '')
        new_twilio_account_sid = request.form.get('twilio_account_sid', '')
        new_twilio_auth_token = request.form.get('twilio_auth_token', '')
        new_twilio_phone_number = request.form.get('twilio_phone_number', '')
        
        # Validate refresh interval
        try:
            refresh_value = int(new_refresh)
            if refresh_value < 1 or refresh_value > 60:
                flash('Refresh interval must be between 1 and 60 minutes', 'danger')
                return render_template('settings.html', 
                                      refresh_interval=current_refresh,
                                      current_timezone=current_timezone,
                                      atera_api_key=atera_api_key,
                                      twilio_account_sid=twilio_account_sid,
                                      twilio_auth_token=twilio_auth_token,
                                      twilio_phone_number=twilio_phone_number,
                                      timezones=pytz.common_timezones)
        except ValueError:
            flash('Refresh interval must be a number', 'danger')
            return render_template('settings.html', 
                                  refresh_interval=current_refresh,
                                  current_timezone=current_timezone,
                                  atera_api_key=atera_api_key,
                                  twilio_account_sid=twilio_account_sid,
                                  twilio_auth_token=twilio_auth_token,
                                  twilio_phone_number=twilio_phone_number,
                                  timezones=pytz.common_timezones)
        
        # Validate timezone
        if new_timezone not in pytz.all_timezones:
            flash('Invalid timezone selected', 'danger')
            return render_template('settings.html', 
                                  refresh_interval=current_refresh,
                                  current_timezone=current_timezone,
                                  atera_api_key=atera_api_key,
                                  twilio_account_sid=twilio_account_sid,
                                  twilio_auth_token=twilio_auth_token,
                                  twilio_phone_number=twilio_phone_number,
                                  timezones=pytz.common_timezones)
        
        # Save refresh interval setting
        save_setting('refresh_interval', new_refresh)
        
        # Save timezone setting
        save_setting('timezone', new_timezone)
        
        # Save API keys
        save_setting('atera_api_key', new_atera_api_key)
        save_setting('twilio_account_sid', new_twilio_account_sid)
        save_setting('twilio_auth_token', new_twilio_auth_token)
        save_setting('twilio_phone_number', new_twilio_phone_number)
        
        # Determine what changed for appropriate message
        api_keys_changed = (atera_api_key != new_atera_api_key or 
                           twilio_account_sid != new_twilio_account_sid or 
                           twilio_auth_token != new_twilio_auth_token or 
                           twilio_phone_number != new_twilio_phone_number)
        timezone_changed = current_timezone != new_timezone
        refresh_changed = current_refresh != int(new_refresh)
        
        # Different message based on what was changed
        if api_keys_changed and timezone_changed and refresh_changed:
            flash('All settings updated successfully. API keys and timezone changes are effective immediately. Refresh interval changes will take effect after restarting the application.', 'success')
        elif api_keys_changed and timezone_changed:
            flash('API keys and timezone updated successfully.', 'success')
        elif api_keys_changed and refresh_changed:
            flash('API keys updated successfully. Refresh interval changes will take effect after restarting the application.', 'success')
        elif timezone_changed and refresh_changed:
            flash('Timezone updated successfully. Refresh interval changes will take effect after restarting the application.', 'success')
        elif api_keys_changed:
            flash('API keys updated successfully.', 'success')
        elif timezone_changed:
            flash('Timezone updated successfully.', 'success')
        elif refresh_changed:
            flash('Refresh interval updated successfully. Restart the application for changes to take effect.', 'success')
        else:
            flash('No changes detected.', 'info')
            
        return redirect(url_for('settings'))
    
    return render_template('settings.html', 
                          refresh_interval=current_refresh,
                          current_timezone=current_timezone,
                          atera_api_key=atera_api_key,
                          twilio_account_sid=twilio_account_sid,
                          twilio_auth_token=twilio_auth_token,
                          twilio_phone_number=twilio_phone_number,
                          timezones=pytz.common_timezones)

@app.route('/business-hours/add', methods=['GET', 'POST'])
@login_required
def add_business_hours():
    if request.method == 'POST':
        day_of_week = int(request.form.get('day_of_week'))
        start_time = datetime.strptime(request.form.get('start_time'), '%H:%M').time()
        end_time = datetime.strptime(request.form.get('end_time'), '%H:%M').time()
        
        # Check if entry for this day already exists
        existing = BusinessHours.query.filter_by(day_of_week=day_of_week).first()
        if existing:
            existing.start_time = start_time
            existing.end_time = end_time
        else:
            new_hours = BusinessHours(
                day_of_week=day_of_week,
                start_time=start_time,
                end_time=end_time
            )
            db.session.add(new_hours)
        
        db.session.commit()
        
        flash('Business hours updated successfully')
        return redirect(url_for('business_hours'))
    
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    return render_template('add_business_hours.html', days=days)

@app.route('/tickets')
@login_required
def tickets():
    tickets = Ticket.query.order_by(Ticket.created_at.desc()).limit(20).all()
    return render_template('tickets.html', tickets=tickets)

@app.route('/tickets/refresh')
@login_required
def refresh_tickets():
    fetch_tickets_from_atera()
    return redirect(url_for('tickets'))

@app.route('/dashboard/refresh-tickets')
@login_required
def dashboard_refresh_tickets():
    """Refresh tickets and return to the dashboard"""
    fetch_tickets_from_atera()
    return redirect(url_for('index'))

@app.route('/business-hours/status')
@login_required
def business_hours_status():
    """Return the current business hours status"""
    is_within_hours = is_business_hours()
    return jsonify({
        'within_business_hours': is_within_hours,
        'current_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/test-atera')
@login_required
def test_atera():
    """Test route to manually fetch and display tickets from Atera API"""
    api_key = os.getenv('ATERA_API_KEY')
    if not api_key:
        return jsonify({'error': 'Atera API key not configured'}), 500
    
    headers = {
        'X-API-KEY': api_key,
        'Accept': 'application/json'
    }
    
    try:
        # Fetch tickets from Atera API with query parameters for open tickets
        response = requests.get(
            'https://app.atera.com/api/v3/tickets',
            headers=headers,
            params={
                'page': 1,
                'itemsInPage': 50,
                'ticketStatus': 'Open'
            }
        )
        
        if response.status_code != 200:
            return jsonify({
                'error': f'Failed to fetch tickets: {response.status_code}',
                'response': response.text
            }), 500
        
        # Return the raw API response
        return jsonify(response.json())
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    # Check if any users exist
    if User.query.count() > 0:
        flash('Setup already completed')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Create admin user
        admin = User(username=username, password=password, is_admin=True)
        db.session.add(admin)
        db.session.commit()
        
        flash('Setup completed successfully. Please log in.')
        return redirect(url_for('login'))
    
    return render_template('setup.html')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Create database tables
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)
