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

