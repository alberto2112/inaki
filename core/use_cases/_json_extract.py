"""Helper de extracción de arrays JSON desde respuestas de LLMs.

Necesario porque los modelos con razonamiento (DeepSeek, o1, etc.) leakean texto
ANTES o DESPUÉS del array — preámbulos, secciones ``## Reasoning``, texto libre.
Un ``rfind(']')`` ingenuo se rompe en cuanto ese texto contiene un ``]`` extra
(markdown links ``[x](url)``, listas, código, etc.), por eso escaneamos desde el
primer ``[`` contando profundidad de brackets e **ignorando** los que caen dentro
de string literals (respeta comillas y escapes de barra invertida).
"""


def extract_json_array(raw: str) -> str | None:
    """Localiza el primer array JSON top-level dentro de ``raw``.

    Escanea desde el primer ``[`` llevando un contador de profundidad de
    brackets. Los caracteres dentro de string literals (delimitados por ``"``
    con soporte de escape ``\\``) se ignoran — así los ``[`` y ``]`` dentro de
    strings o texto embebido no confunden el contador.

    Args:
        raw: Texto crudo proveniente del LLM, que puede contener prefijos de
            razonamiento, texto libre, o formato markdown alrededor del JSON.

    Returns:
        El substring que corresponde al primer array JSON balanceado encontrado,
        o ``None`` si no se encuentra ningún array.
    """
    start = raw.find("[")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None
