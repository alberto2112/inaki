"""Gateway de modelos de lenguaje: ``BaseLLMProvider``, ``ResolvedLLMConfig`` y providers.

Convención de autodiscovery (la aplica ``LLMProviderFactory``): cada módulo de
provider declara ``PROVIDER_NAME`` y exactamente una clase que hereda de
``BaseLLMProvider`` definida en ese módulo. ``ILLMProvider`` y ``LLMResponse``
siguen en ``core/`` porque los consume el turno.
"""
