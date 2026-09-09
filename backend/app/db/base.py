from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# Nomenclatura previsível para constraints/índices: mantém as migrations SQL
# escritas à mão e os modelos falando o mesmo nome de objeto.
NAMING_CONVENTION = {
    "ix": "%(table_name)s_%(column_0_N_name)s_idx",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION, schema="public")

    type_annotation_map: ClassVar[dict] = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }
