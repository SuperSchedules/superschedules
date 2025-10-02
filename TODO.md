# TODO

## Email Verification for Account Creation

Check the frontend account creation flow for email verification functionality. The outgoing email verification may be:
- Not implemented at all in the backend API
- Not requiring verification (accounts are auto-activated)
- Missing email sending configuration

**Files to check:**
- `superschedules_frontend/` - Account creation UI/form
- `superschedules/api/views.py` - User registration endpoint
- `superschedules/config/settings.py` - Email backend configuration (EMAIL_HOST, etc.)

**Current email config in settings.py:**
- `EMAIL_BACKEND` is configured for SMTP (Gmail)
- `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` are read from environment
- May need to add email verification step to registration flow
