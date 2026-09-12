"""Broadcast LAN entre instancias — la respuesta a que Telegram no deje hablar a dos bots.

Transporte TCP con HMAC (``tcp``), buffer efímero de contexto (``buffer``), tipos
de evento (``port``), la política de emisión (``egress``) y el trigger de ingress
del bot (``ingress``). Es un concern EXCLUSIVO de este canal.
"""
