"""Phase 18.5: per-field fill details on ``applications``.

The integer counters ``fields_filled`` / ``fields_total`` told us
*how many* fields the form-filler touched, but not which ones nor why
the misses missed. Add a JSONB ``fill_details`` column to persist a
per-field record straight from :class:`FieldMapping`:

  [
    {
      "label": "Full name",
      "data_key": "identity.full_name",
      "value": "Liam Frost",
      "filled": true,
      "error": ""
    },
    {
      "label": "Years of experience",
      "data_key": "qa.years_experience",
      "value": "",
      "filled": false,
      "error": "no matching profile field"
    }
  ]

The Review queue UI uses this to let the operator expand the
"N of M fields filled" badge and see exactly what we attempted.

Revision ID: c3a7e1f2b048
Revises: b8d2f9e15c33
Create Date: 2026-05-21 14:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a7e1f2b048"
down_revision: str | Sequence[str] | None = "b8d2f9e15c33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "fill_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("applications", "fill_details")
