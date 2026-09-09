"""Agentes: delegación entre agentes, despacho de turnos por scope y kill-switch.

``dispatcher`` (un turno a un agente, serializado por scope; lo comparten el
scheduler y la cola de background), ``scope_registry`` (busy/idle y cancelación
por conversación) y ``delegation/`` (la tool ``delegate`` y la cola en
background). Los ports que consume el turno (``IScopeRegistry``,
``IBackgroundDelegationQueue``, ``ILLMDispatcher``) siguen en ``core/``.
"""
