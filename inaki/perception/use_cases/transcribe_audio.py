"""``TranscribeAudioUseCase`` — transcribir un audio recibido por CUALQUIER canal.

Antes esta lógica vivía inline en el handler de voz de Telegram (size-check,
idioma, provider, transcripción vacía): un canal nuevo con audio (Slack, la app
Android) habría tenido que copiarla. Acá vive una vez; el canal solo entrega los
bytes y decide qué decirle al usuario ante cada error.
"""

from __future__ import annotations

from inaki.perception.ports.transcription import ITranscriptionProvider
from inaki.perception.settings import TranscriptionSettings
from inaki.shared.errors import TranscriptionError, TranscriptionFileTooLargeError


class EmptyTranscriptionError(TranscriptionError):
    """El provider respondió sin texto: no hay nada que ejecutar."""


class TranscribeAudioUseCase:
    def __init__(self, provider: ITranscriptionProvider, settings: TranscriptionSettings) -> None:
        self._provider = provider
        self._settings = settings

    @property
    def max_audio_bytes(self) -> int:
        return self._settings.max_audio_mb * 1024 * 1024

    def check_size(self, size_bytes: int) -> None:
        """Lanza ``TranscriptionFileTooLargeError`` si el audio supera el límite.

        Separado de ``execute`` para que el canal pueda rechazar ANTES de mostrar
        actividad al usuario (reacción, "escribiendo…"), con el tamaño que
        declara la plataforma sin descargar nada.
        """
        if size_bytes > self.max_audio_bytes:
            raise TranscriptionFileTooLargeError(size_bytes, self.max_audio_bytes)

    async def execute(
        self, audio: bytes, mime_type: str, *, declared_size: int | None = None
    ) -> str:
        """Transcribe ``audio``. Errores del provider propagan como ``TranscriptionError``.

        Raises:
            TranscriptionFileTooLargeError: supera ``max_audio_mb``.
            EmptyTranscriptionError: el provider devolvió texto vacío.
            TranscriptionError: fallo del provider (red, formato, cuota).
        """
        self.check_size(declared_size or len(audio))
        texto = await self._provider.transcribe(audio, mime_type, language=self._settings.language)
        if not texto or not texto.strip():
            raise EmptyTranscriptionError("La transcripción vino vacía.")
        return texto
