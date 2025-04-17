// Main JavaScript for On-Call Ticket Monitor

document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Initialize popovers
    var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });

    // Auto-close alerts after 5 seconds
    setTimeout(function() {
        document.querySelectorAll('.alert-dismissible').forEach(function(alert) {
            var bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        });
    }, 5000);

    // Add confirm dialog to delete buttons
    document.querySelectorAll('.btn-delete').forEach(function(button) {
        button.addEventListener('click', function(e) {
            if (!confirm('Are you sure you want to delete this item?')) {
                e.preventDefault();
            }
        });
    });
});

// Format date for display
function formatDate(dateString) {
    const options = { 
        year: 'numeric', 
        month: 'short', 
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    };
    return new Date(dateString).toLocaleDateString(undefined, options);
}

// Check if current time is within business hours (client-side estimation)
function isBusinessHours() {
    const now = new Date();
    const day = now.getDay(); // 0 = Sunday, 6 = Saturday
    const hour = now.getHours();
    
    // Default business hours (9 AM - 5 PM, Monday to Friday)
    // This is just a client-side estimation - actual check is done server-side
    return day > 0 && day < 6 && hour >= 9 && hour < 17;
}

// Update business hours status indicator
function updateBusinessHoursStatus() {
    const statusElement = document.getElementById('business-hours-status');
    if (!statusElement) return;
    
    if (isBusinessHours()) {
        statusElement.textContent = 'Within Business Hours';
        statusElement.className = 'badge bg-success';
    } else {
        statusElement.textContent = 'After Hours';
        statusElement.className = 'badge bg-danger';
    }
}

// Call this function when page loads and set interval to update
if (document.getElementById('business-hours-status')) {
    updateBusinessHoursStatus();
    setInterval(updateBusinessHoursStatus, 60000); // Update every minute
}

// Handle refresh tickets button
document.addEventListener('DOMContentLoaded', function() {
    const refreshButton = document.getElementById('refresh-tickets');
    if (refreshButton) {
        refreshButton.addEventListener('click', function() {
            // Show loading state
            const originalText = refreshButton.textContent;
            refreshButton.disabled = true;
            refreshButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Refreshing...';
            
            // Make AJAX request to refresh tickets
            fetch('/refresh-tickets')
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // Update last check time
                        const lastCheckElement = document.getElementById('last-check-time');
                        if (lastCheckElement) {
                            lastCheckElement.textContent = data.last_check;
                        }
                        
                        // Show success message
                        showAlert('success', 'Tickets refreshed successfully! Reloading page...');
                        
                        // Reload page after a short delay to show new tickets
                        setTimeout(() => {
                            window.location.reload();
                        }, 1500);
                    } else {
                        // Show error message
                        showAlert('danger', 'Error refreshing tickets: ' + data.message);
                        refreshButton.disabled = false;
                        refreshButton.textContent = originalText;
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    showAlert('danger', 'Error refreshing tickets. Please try again.');
                    refreshButton.disabled = false;
                    refreshButton.textContent = originalText;
                });
        });
    }
});

// Function to show alert messages
function showAlert(type, message) {
    // Create alert element
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.role = 'alert';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    
    // Insert at the top of the content area
    const contentArea = document.querySelector('.container');
    if (contentArea) {
        contentArea.insertBefore(alertDiv, contentArea.firstChild);
        
        // Auto-dismiss after 5 seconds
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alertDiv);
            bsAlert.close();
        }, 5000);
    }
}
