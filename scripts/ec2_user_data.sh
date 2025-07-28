#!/bin/bash
set -e

# 1. Update & essentials
apt-get update -y
apt-get upgrade -y
apt-get install -y git software-properties-common curl awscli

# Fetch Git SSH key from AWS Secrets Manager
SECRET_NAME="github_ssh_key"
mkdir -p /home/ubuntu/.ssh
aws secretsmanager get-secret-value --secret-id "$SECRET_NAME" --query SecretString --output text > /home/ubuntu/.ssh/id_rsa
chmod 600 /home/ubuntu/.ssh/id_rsa
cat <<EOF >> /home/ubuntu/.ssh/config
Host github.com
  IdentityFile /home/ubuntu/.ssh/id_rsa
  StrictHostKeyChecking no
EOF

# 2. Install PostgreSQL
apt-get install -y postgresql postgresql-contrib libpq-dev

# Optionally create a DB & user (match these to your settings.py)
sudo -u postgres psql -c "CREATE USER ec2user WITH PASSWORD 'changeme';"
sudo -u postgres psql -c "CREATE DATABASE myappdb OWNER ec2user;"

# 3. Install Python 3.11 via deadsnakes PPA
add-apt-repository ppa:deadsnakes/ppa -y
apt-get update -y
apt-get install -y python3.11 python3.11-venv python3.11-dev build-essential

# Make python3.11 the default python3 (optional)
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# 4. Clone your repo & set up virtualenv
cd /home/ubuntu
git clone git@github.com:gkirkpatrick/superschedules.git app
cd app

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

# 5. Django migrations & runserver
export DJANGO_SETTINGS_MODULE=config.settings
python manage.py migrate

# Launch dev server (backgrounded)
nohup python manage.py runserver 0.0.0.0:8000 \
  > django.log 2>&1 &
