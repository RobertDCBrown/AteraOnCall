"""
Production server script for On-Call Ticket Monitor using Waitress.
Run this script to start the application in production mode.
"""
import os
from waitress import serve
from app import app

# Configuration
PORT = 8000  # Change this to your preferred port
HOST = '0.0.0.0'  # Listen on all interfaces

if __name__ == '__main__':
    print(f"Starting On-Call Ticket Monitor in production mode on {HOST}:{PORT}")
    print("Press Ctrl+C to stop the server")
    
    # Start the server
    serve(app, host=HOST, port=PORT, threads=4)
