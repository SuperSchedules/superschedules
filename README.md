# SuperSchedules

This repository contains the SuperSchedules Django project.

## EC2 Setup

The `scripts/ec2_user_data.sh` script contains a user-data example for provisioning
an EC2 instance. It installs dependencies, pulls this repository, and launches the
Django server. Copy the script's contents into the **User data** field when creating
an EC2 instance.

The script expects an SSH key stored in AWS Secrets Manager under the name
`github_ssh_key`. The key is used to authenticate with GitHub when cloning the
`gkirkpatrick/superschedules` repository. Adjust the `SECRET_NAME` variable in the
script if your secret uses a different name.

`DJANGO_SETTINGS_MODULE` is set to `config.settings` which matches this project.

```bash
# Example usage when creating an instance
cat scripts/ec2_user_data.sh
```
