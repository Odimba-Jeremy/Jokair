# Les connexions SSE restent ouvertes : un worker "sync" monopolise le
# processus et finit par atteindre le délai Gunicorn. Les threads permettent
# de servir les autres requêtes pendant qu'un client écoute les événements.
worker_class = "gthread"
threads = 4
timeout = 120
keepalive = 5
