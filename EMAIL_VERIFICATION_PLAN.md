# Email Verification Implementation Plan

## Current State
- ❌ No email service configured (EMAIL_HOST_USER and EMAIL_HOST_PASSWORD are empty)
- ❌ No email verification system for new accounts
- ❌ Password reset tries to send email but fails silently
- ✅ Frontend shows "verify your account" page but no actual verification flow
- ✅ Password reset token generation exists but email won't send

## AWS SES Setup Required

### 1. AWS SES Configuration
- [ ] Create AWS SES identity (verify domain: eventzombie.com)
- [ ] Move out of SES sandbox (request production access)
- [ ] Create SMTP credentials in SES console
- [ ] Configure SPF/DKIM records in Route53 for email deliverability
- [ ] Set up bounce/complaint handling (optional but recommended)

### 2. Backend Changes (Django)

#### Secrets Management
**Use AWS Secrets Manager for SMTP credentials:**

1. Create secret in AWS Secrets Manager:
```bash
aws secretsmanager create-secret \
  --name prod/superschedules/email \
  --description "SES SMTP credentials for EventZombie" \
  --secret-string '{
    "EMAIL_HOST_USER": "<SES_SMTP_USERNAME>",
    "EMAIL_HOST_PASSWORD": "<SES_SMTP_PASSWORD>"
  }'
```

2. Update IAM role (terraform/prod/iam.tf) to allow EC2 instances to read the secret:
```hcl
resource "aws_iam_role_policy" "ec2_secrets" {
  name = "ec2-secrets-access"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:*:secret:prod/superschedules/email-*"
      }
    ]
  })
}
```

3. Update user_data.sh.tftpl to fetch secrets and add to environment:
```bash
# Fetch email credentials from Secrets Manager
EMAIL_SECRET=$(aws secretsmanager get-secret-value \
  --secret-id prod/superschedules/email \
  --region ${region} \
  --query SecretString \
  --output text)

EMAIL_HOST_USER=$(echo $EMAIL_SECRET | jq -r '.EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD=$(echo $EMAIL_SECRET | jq -r '.EMAIL_HOST_PASSWORD')
```

4. Add to django service environment in docker-compose.yml section:
```yaml
django:
  environment:
    # ... existing vars ...
    - EMAIL_HOST_USER=$${EMAIL_HOST_USER}
    - EMAIL_HOST_PASSWORD=$${EMAIL_HOST_PASSWORD}
    - DEFAULT_FROM_EMAIL=noreply@eventzombie.com
    - EMAIL_HOST=email-smtp.us-east-1.amazonaws.com
    - EMAIL_PORT=587
    - EMAIL_USE_TLS=True
```

#### Local Development
Add to local `.env` file (NOT committed to git):
```bash
EMAIL_HOST_USER=<local_or_ses_username>
EMAIL_HOST_PASSWORD=<local_or_ses_password>
DEFAULT_FROM_EMAIL=noreply@eventzombie.com
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend  # For testing
```

#### New API Endpoints (api/views.py)
- [ ] `POST /api/v1/users/verify/resend/` - Resend verification email
- [ ] `POST /api/v1/users/verify/{token}/` - Verify email with token

#### Database Changes
- [ ] Add `email_verified` boolean field to User model (default=False)
- [ ] Add `email_verification_token` field (or use signing.dumps for stateless tokens)
- [ ] Migration to add fields

#### User Registration Flow Update
Current:
```python
# api/views.py create_user()
user = User.objects.create_user(...)
# No email sent
```

New:
```python
user = User.objects.create_user(...)
user.email_verified = False
user.save()

# Generate verification token
token = signing.dumps({"user_id": user.id}, salt="email-verification")
verify_link = f"{settings.FRONTEND_URL}/verify-email?token={token}"

# Send verification email
send_mail(
    "Verify Your EventZombie Account",
    f"Welcome! Click to verify: {verify_link}",
    settings.DEFAULT_FROM_EMAIL,
    [user.email],
    fail_silently=False,  # Let it raise errors in dev
)
```

#### Login Check
- [ ] Update login endpoint to check `email_verified` before issuing token
- [ ] Return helpful error: "Please verify your email address"

### 3. Frontend Changes (React)

#### New Page/Component
- [ ] `VerifyEmail.tsx` - handles `/verify-email?token=xxx` route
  - Extracts token from URL params
  - POSTs to `/api/v1/users/verify/{token}/`
  - Shows success/error message
  - Redirects to login on success

#### Update Routes (App.tsx)
```tsx
<Route path="/verify-email" element={<VerifyEmail />} />
```

#### Update API Constants
```typescript
export const AUTH_ENDPOINTS = {
  // ... existing
  verifyEmail: `${API_ROOT}/users/verify/`,
  resendVerification: `${API_ROOT}/users/verify/resend/`,
};
```

#### Improve VerifyAccount.tsx
Current: Just shows "check your email"
New:
- Show user's email address
- Add "Resend verification email" button
- Better instructions

#### Login Error Handling
- [ ] Handle "email not verified" error from backend
- [ ] Show message with link to resend verification

### 4. Email Templates

#### Verification Email
```
Subject: Verify Your EventZombie Account

Hi there!

Welcome to EventZombie - your friendly guide to local events!

Please verify your email address by clicking the link below:
{verify_link}

This link will expire in 24 hours.

If you didn't create an account, you can safely ignore this email.

Cheers,
The EventZombie Team
🧟
```

#### Password Reset Email (improve existing)
```
Subject: Reset Your EventZombie Password

Hi there!

We received a request to reset your password. Click the link below:
{reset_link}

This link will expire in 1 hour.

If you didn't request this, you can safely ignore this email.

Cheers,
The EventZombie Team
🧟
```

### 5. Testing Plan

#### Local Development
- [ ] Use console backend for testing: `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`
- [ ] Verify token generation/validation works
- [ ] Test token expiry (add max_age to signing.loads)

#### Production Testing
- [ ] Send test email from SES
- [ ] Verify SPF/DKIM records
- [ ] Test full flow: register → receive email → verify → login
- [ ] Test resend verification email
- [ ] Test password reset email

### 6. Security Considerations
- [ ] Token expiry: 24 hours for email verification
- [ ] Token expiry: 1 hour for password reset
- [ ] Rate limiting on verification email resend (prevent abuse)
- [ ] Prevent login for unverified accounts
- [ ] Log verification attempts for monitoring

## Implementation Order
1. ✅ Create this plan document
2. AWS SES setup (requires AWS console access)
3. Backend: Add email_verified field + migration
4. Backend: Update registration to send verification email
5. Backend: Add verification endpoint
6. Frontend: Add VerifyEmail page
7. Frontend: Update VerifyAccount page with resend button
8. Test locally with console backend
9. Deploy backend + frontend
10. Configure production EMAIL env vars
11. Test end-to-end in production

## Estimated Time
- AWS SES setup: 30-60 minutes (includes DNS propagation wait)
- Backend implementation: 2-3 hours
- Frontend implementation: 1-2 hours
- Testing + debugging: 1-2 hours
- **Total: ~6-8 hours**

## Notes
- Consider using Django packages like `django-allauth` for future (handles social auth + email verification)
- SES is in sandbox by default - need to request production access (can take 24 hours)
- In sandbox, can only send to verified email addresses
- Consider adding email templates with HTML for better appearance

---
*Created: 2025-12-07*
*Status: Planning*
