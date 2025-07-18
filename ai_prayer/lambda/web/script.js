document.addEventListener('DOMContentLoaded', function() {
    const signupForm = document.getElementById('signup-form');
    const emailInput = document.getElementById('email-input');
    const formMessage = document.getElementById('form-message');
    const submitButton = signupForm.querySelector('button[type="submit"]');

    signupForm.addEventListener('submit', function(event) {
        event.preventDefault();
        
        const email = emailInput.value.trim();
        const apiGatewayUrl = window.config.apiGatewayUrl;
        const endpoint = `${apiGatewayUrl}/signup`;

        if (email && apiGatewayUrl) {
            submitButton.disabled = true;
            submitButton.textContent = 'Submitting...';

            fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ email: email })
            })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(err => { throw new Error(err.message || 'An unknown error occurred.') });
                }
                return response.json();
            })
            .then(data => {
                console.log('Success:', data);
                formMessage.textContent = "Thank you! Please check your email for a confirmation link to complete your subscription.";
                formMessage.className = 'mt-4 success-message';
                emailInput.value = '';

                // Fire Google Analytics event for successful signup
                if (typeof gtag === 'function') {
                    gtag('event', 'sign_up', {
                        'method': 'email'
                    });
                }
            })
            .catch(error => {
                console.error('Error:', error);
                formMessage.textContent = `Error: ${error.message}`;
                formMessage.className = 'mt-4 error-message';
            })
            .finally(() => {
                submitButton.disabled = false;
                submitButton.textContent = 'Start My Journey';
            });

        } else if (!apiGatewayUrl) {
            formMessage.textContent = "API endpoint is not configured. Please wait a moment and try again.";
            formMessage.className = 'mt-4 error-message';
        } else {
            formMessage.textContent = "Please enter a valid email address.";
            formMessage.className = 'mt-4 error-message';
        }
    });
});

// Add some simple styling for messages
const style = document.createElement('style');
style.innerHTML = `
    .success-message {
        color: #155724;
        background-color: #d4edda;
        border-color: #c3e6cb;
        padding: 1rem;
        border-radius: .25rem;
    }
    .error-message {
        color: #721c24;
        background-color: #f8d7da;
        border-color: #f5c6cb;
        padding: 1rem;
        border-radius: .25rem;
    }
`;
document.head.appendChild(style);

document.addEventListener('DOMContentLoaded', function() {
    const feedbackForm = document.getElementById('feedback-form');
    const feedbackInput = document.getElementById('feedback-input');
    const feedbackEmail = document.getElementById('feedback-email');
    const feedbackMessage = document.getElementById('feedback-message');
    const feedbackSubmitButton = feedbackForm.querySelector('button[type="submit"]');

    feedbackForm.addEventListener('submit', function(event) {
        event.preventDefault();

        const feedbackText = feedbackInput.value.trim();
        const userEmail = feedbackEmail.value.trim();
        const apiGatewayUrl = window.config.apiGatewayUrl;
        const endpoint = `${apiGatewayUrl}/feedback`;

        if (feedbackText && apiGatewayUrl) {
            feedbackSubmitButton.disabled = true;
            feedbackSubmitButton.textContent = 'Sending...';

            fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    feedback: feedbackText,
                    email: userEmail
                })
            })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(err => { throw new Error(err.message || 'An unknown error occurred.') });
                }
                return response.json();
            })
            .then(data => {
                feedbackMessage.textContent = "Thank you for your feedback!";
                feedbackMessage.className = 'success-message';
                feedbackInput.value = '';
                feedbackEmail.value = '';
            })
            .catch(error => {
                feedbackMessage.textContent = `Error: ${error.message}`;
                feedbackMessage.className = 'error-message';
            })
            .finally(() => {
                feedbackSubmitButton.disabled = false;
                feedbackSubmitButton.textContent = 'Send Feedback';
            });
        } else if (!apiGatewayUrl) {
            feedbackMessage.textContent = "API endpoint is not configured.";
            feedbackMessage.className = 'error-message';
        } else {
            feedbackMessage.textContent = "Please enter your feedback before sending.";
            feedbackMessage.className = 'error-message';
        }
    });
});

