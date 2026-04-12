"""Tests para cosine_similarity."""

from core.domain.services.similarity import cosine_similarity


def test_vectores_identicos_retornan_uno():
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == 1.0


def test_vectores_ortogonales_retornan_cero():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_vectores_opuestos_retornan_menos_uno():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-6


def test_vector_cero_retorna_cero():
    a = [0.0, 0.0, 0.0]
    b = [1.0, 2.0, 3.0]
    assert cosine_similarity(a, b) == 0.0
