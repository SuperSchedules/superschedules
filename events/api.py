from rest_framework import serializers, viewsets
from .models import Event


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ['id', 'title', 'description', 'location', 'start_time', 'end_time', 'url']


class EventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.all().order_by('start_time')
    serializer_class = EventSerializer

