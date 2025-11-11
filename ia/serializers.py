# ia/serializers.py
from rest_framework import serializers

class PrediccionVentasSerializer(serializers.Serializer):
    fecha = serializers.DateField(required=False)
