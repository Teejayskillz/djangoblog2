from django.apps import AppConfig



class CoreConfig(AppConfig): # Replace CoreConfig with your actual AppConfig class name
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core' # Replace 'core' with your actual app name

    def ready(self):
        import core.signals # This line connects your signals!
        # Or: import your_app_name.signals'

        print("Core app is ready, signals loaded.")