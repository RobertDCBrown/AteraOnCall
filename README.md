# On-Call Ticket Monitor

A Flask web application for monitoring tickets from Atera and sending SMS notifications to on-call technicians when tickets come in after business hours or on holidays.

## Features

- **Ticket Monitoring**: Integration with Atera API to fetch and track support tickets
- **SMS Notifications**: Integration with Twilio for sending text notifications to on-call technicians
- **Multiple On-Call Technicians**: Support for multiple technicians with overlapping schedules
- **Business Hours Configuration**: Configure business hours for each day of the week
- **Holiday Calendar**: Manage holidays with automatic notification handling
- **Web-Based Configuration**: Manage all settings through a user-friendly web interface
- **Comprehensive Logging**: Detailed logging for troubleshooting and monitoring

## Setup Instructions

### Prerequisites

- Python 3.8 or higher
- Atera API key
- Twilio account (Account SID, Auth Token, and phone number)

### Installation

1. Clone the repository or download the source code

2. Create a virtual environment:
   ```
   python -m venv venv
   ```

3. Activate the virtual environment:
   - Windows: `venv\Scripts\activate`
   - macOS/Linux: `source venv/bin/activate`

4. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

5. Create a `.env` file from the example:
   ```
   copy example.env .env
   ```

6. Edit the `.env` file to set your Flask secret key:
   ```
   SECRET_KEY=your-secret-key-change-this-in-production
   ```

### Running the Application

1. Start the Flask application:
   ```
   python app.py
   ```

2. Open a web browser and navigate to:
   ```
   http://localhost:5000/setup
   ```

3. On first run, you'll be prompted to create an admin account

## Configuration

### API Integration

#### Web-Based API Key Management

All API keys are managed through the Settings page in the web interface:

- **Atera API Key**: Required to fetch tickets from the Atera ticketing system
- **Twilio Account SID**: Required for Twilio SMS integration
- **Twilio Auth Token**: Required for Twilio authentication
- **Twilio Phone Number**: The phone number used to send SMS notifications

This approach offers several advantages:
- No need to restart the application after changing API keys
- Secure storage in the database rather than in files
- Easy management through a user-friendly interface

#### Error Handling and Logging

The application includes comprehensive error handling and logging for API integrations:

- **Atera API**: Detailed logging of API requests, responses, and errors with specific handling for timeouts, connection issues, and authentication problems.
- **Twilio API**: Specific error handling for common Twilio error codes (invalid phone numbers, authentication issues, etc.) with clear error messages.
- **Database Operations**: Transaction logging with proper error handling and rollback mechanisms.

All logs include appropriate severity levels (info, debug, warning, error, critical) to make troubleshooting easier.

Note: For backward compatibility only, API keys can still be provided in the `.env` file, but this is not recommended and is only used as a fallback if keys are not found in the database.

## Production Deployment

### Deploying with Waitress (Windows)

For production deployment on Windows, we recommend using Waitress as the WSGI server. Follow these steps:

1. **Install Waitress**:

   ```bash
   pip install waitress
   ```

2. **Run the application using the provided script**:

   ```bash
   # Option 1: Using the Python script
   python run_production.py
   
   # Option 2: Using the batch file
   start_server.bat
   ```

3. **Access the application**:

   The application will be available at `http://server-ip:8000`

### Production Configuration

1. **Set a strong secret key**:

   Generate a secure random key and set it in your `.env` file:
   ```
   SECRET_KEY=your-secure-random-key
   ```

2. **Configure the database**:

   By default, the application uses SQLite. For production, consider:
   - Using a more robust database like PostgreSQL or MySQL
   - Setting up regular database backups

3. **Set up as a Windows Service (optional)**:

   For automatic startup and recovery, consider using NSSM (Non-Sucking Service Manager) to run the application as a Windows service:

   ```bash
   # Install NSSM
   # Download from http://nssm.cc/
   
   # Install the service
   nssm install OnCallMonitor "C:\Path\To\Python\python.exe" "C:\Path\To\App\run_production.py"
   
   # Configure service details
   nssm set OnCallMonitor Description "On-Call Ticket Monitor Service"
   nssm set OnCallMonitor AppDirectory "C:\Path\To\App"
   
   # Start the service
   nssm start OnCallMonitor
   ```

4. **Network Configuration**:

   - Configure your firewall to allow traffic on port 8000
   - For public access, consider setting up a reverse proxy with Nginx or IIS

### Business Hours and Holidays

#### Business Hours
Configure business hours for each day of the week through the web interface. Tickets that come in outside of these hours will trigger SMS notifications to the on-call technicians.

#### Holiday Calendar
Manage holidays through the dedicated Holiday Calendar page. The system will:
- Treat holidays as outside business hours for notification purposes
- Send notifications to all on-call technicians on holidays regardless of time
- Include holiday information in SMS notifications

### On-Call Schedule

Create schedules for technicians to be on-call. The application supports:

- **Multiple On-Call Technicians**: Configure overlapping schedules with multiple technicians on-call simultaneously
- **Flexible Scheduling**: Set specific date ranges for each technician's on-call period
- **Redundant Notifications**: All on-call technicians receive notifications for after-hours or holiday tickets

The application automatically determines who is currently on-call based on these schedules and displays the current on-call technicians on the dashboard.

## Service Management

### Web-Based Restart

The application includes a convenient restart feature accessible from the web interface:

- **Restart Button**: Located on the Settings page for easy access
- **Confirmation Dialog**: Prevents accidental restarts
- **Status Updates**: Shows real-time status during the restart process
- **Automatic Reconnection**: The page automatically refreshes after restart

This feature allows you to restart the service after making configuration changes without needing to access the server directly.

## Background Jobs

The application uses APScheduler to run background jobs:

- **Ticket Fetching**: Runs at configurable intervals (default: 5 minutes) to check for new tickets
- **Notification Sending**: Automatically sends SMS to on-call technicians for after-hours or holiday tickets
- **Performance Monitoring**: Tracks job execution time and provides detailed logs

## Security Considerations

- **Authentication**: This application uses basic authentication. For production use, consider implementing more robust authentication.
- **API Keys**: API keys and credentials are stored securely in the database. For backward compatibility, they can also be provided in the `.env` file.
- **Error Handling**: Comprehensive error handling prevents exposing sensitive information in error messages.
- **Logging**: Detailed logging helps identify security issues, but ensure log files are properly secured and rotated.
- **Production Deployment**: For production, set `debug=False` in the Flask application and use a proper WSGI server like Waitress (Windows) or Gunicorn (Linux).
- **Service Account**: When running as a Windows service, consider using a dedicated service account with limited permissions.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
