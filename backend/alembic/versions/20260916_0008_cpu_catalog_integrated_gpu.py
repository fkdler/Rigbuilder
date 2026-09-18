"""expose cpu_spec.integrated_gpu on the agent read surface

Revision ID: 0008_cpu_catalog_integrated_gpu
Revises: 0007_create_query_jobs
Create Date: 2026-09-16

The office scenario cannot be decided without knowing whether a CPU has an
integrated GPU, but `agent_catalog.cpu_catalog` never exposed the column, so a
model could not even query it.  The field `cpu.integrated_gpu` is now registered
in `truth.field_definition`, and this revision publishes the value.

``integrated_gpu`` is appended at the END of the select list on purpose.  PostgreSQL
maps ``CREATE OR REPLACE VIEW`` columns by position, so inserting the column in the
middle would silently rename every column after it.
"""

from alembic import op

revision = "0008_cpu_catalog_integrated_gpu"
down_revision = "0007_create_query_jobs"
branch_labels = None
depends_on = None


WITHOUT_IGPU = """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,o.entity_key AS manufacturer_key,
        c.architecture,c.socket,c.cores_total,c.threads,c.base_clock_mhz,c.boost_clock_mhz,c.base_power_w,c.max_power_w,e.release_date,e.lifecycle_status,e.recommendable
        FROM truth.catalog_entity e JOIN truth.hardware h ON h.entity_id=e.id JOIN truth.cpu_spec c ON c.hardware_id=e.id
        LEFT JOIN truth.catalog_entity o ON o.id=h.manufacturer_id"""

WITH_IGPU = """SELECT e.id AS entity_id,e.entity_key,e.canonical_name AS name,o.entity_key AS manufacturer_key,
        c.architecture,c.socket,c.cores_total,c.threads,c.base_clock_mhz,c.boost_clock_mhz,c.base_power_w,c.max_power_w,e.release_date,e.lifecycle_status,e.recommendable,
        c.integrated_gpu
        FROM truth.catalog_entity e JOIN truth.hardware h ON h.entity_id=e.id JOIN truth.cpu_spec c ON c.hardware_id=e.id
        LEFT JOIN truth.catalog_entity o ON o.id=h.manufacturer_id"""


def upgrade() -> None:
    op.execute(f"CREATE OR REPLACE VIEW agent_catalog.cpu_catalog AS {WITH_IGPU}")


def downgrade() -> None:
    # Dropping the trailing column is legal; the remaining ones keep their positions.
    op.execute(f"CREATE OR REPLACE VIEW agent_catalog.cpu_catalog AS {WITHOUT_IGPU}")
