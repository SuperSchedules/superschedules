import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from model_bakery import baker
from django.test import TestCase
from django.test.utils import setup_test_environment
from django.core.management import call_command

setup_test_environment()
call_command('migrate', verbosity=0)


class AuthTests(TestCase):
    def test_jwt_auth_endpoints(self):
        User = get_user_model()
        password = 'strong-pass'
        user = baker.make(User, username='authuser')
        user.set_password(password)
        user.save()

        client = APIClient()
        resp = client.post('/api/token/', {'username': user.username, 'password': password}, format='json')
        assert resp.status_code == 200
        assert 'access' in resp.data
        assert 'refresh' in resp.data

        refresh = resp.data['refresh']
        resp = client.post('/api/token/refresh/', {'refresh': refresh}, format='json')
        assert resp.status_code == 200
        assert 'access' in resp.data

