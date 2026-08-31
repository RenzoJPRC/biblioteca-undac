import time

class SimpleTTLCache:
    def __init__(self, ttl_seconds):
        self.ttl_seconds = ttl_seconds
        self.cache = {}

    def get(self, key):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl_seconds:
                return data
            else:
                del self.cache[key]
        return None

    def set(self, key, value):
        self.cache[key] = (value, time.time())

    def clear(self, key=None):
        if key:
            if key in self.cache:
                del self.cache[key]
        else:
            self.cache.clear()

global_cache = SimpleTTLCache(ttl_seconds=60) # 1 minuto de caché por defecto
