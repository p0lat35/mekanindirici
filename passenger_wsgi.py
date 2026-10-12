import sys
import os

# Mevcut dizini Python yoluna ekle
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Flask uygulamasını 'application' olarak dışa aktar (cPanel / Phusion Passenger standardı)
from server import app as application
