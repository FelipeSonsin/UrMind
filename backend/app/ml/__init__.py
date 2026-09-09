"""Ferramental de dados e avaliação de modelos (MASTER_PLAN §8 e §10).

Separado de `app/services/` de propósito: aqui não há regra de negócio do
runtime, e sim o que sustenta o ciclo de dataset → treino → promoção —
mapeamento de taxonomia, split sem vazamento e métricas de detecção.
"""
