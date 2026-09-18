"""publish relative performance and the pairing policy on the agent read surface

Revision ID: 0009_performance_surface
Revises: 0008_cpu_catalog_integrated_gpu
Create Date: 2026-09-16

The catalogue used to hold no *relative* number at all.  ``catalogue_position.py``
could report "6 cores, 65 W" but had nothing to compare it with, so a question like
"is this good enough?" could only be answered from the model's training priors --
which is why office requests kept coming back with an i9 and gaming builds with no
GPU.  ``benchmark_run`` existed but was empty.

This revision adds no measurement.  It publishes three derived surfaces over data
that now exists:

``performance_metric``
    Long form: one row per (part, metric).  Generic on purpose -- a new source
    flows through without touching this view.

``performance_ranking``
    Wide form: the score, the publisher's rank together with the population that
    rank was taken from, a market percentile, and ``index_100`` -- a *policy
    anchored* relative performance scale, interpolated from
    ``truth.performance_anchor``.

    Two other scales were tried first and both failed:

    * a percentile inside our own catalogue is skewed by catalogue composition --
      an ordinary i5-12400 lands at the bottom of a catalogue that is mostly
      high-end, so every mainstream build would read as lopsided;
    * a percentile inside the publisher's population is skewed by *its*
      composition -- the GPU population is thousands of integrated and
      decades-old parts, so every discrete card we hold reads as "flagship" and
      the ratio between the two categories collapses to about 1 for everything.

    An anchored scale avoids both, because the anchors are chosen by hand to say
    what "entry", "mainstream" and "flagship" mean *today* on one shared axis.
    They are product policy, versioned and auditable, exactly like the office-fit
    weights that already live in code -- not measurements.  The publisher's own
    percentiles are still published alongside as ``market_percentile``,
    ``class_percentile`` and ``catalogue_percentile`` so nothing is hidden.

``balance_rule_catalog``
    The pairing policy as data rather than as model judgement: for a use case,
    the acceptable band of ``gpu index / cpu index`` plus the minimum acceptable
    index for each side.  The values are product policy, versioned and auditable,
    not measurements -- the same standing as the office-fit weights that already
    live in code.

``entity_alias_catalog``
    Source names mapped onto our entity, so a lookup does not have to guess with
    ``LIKE``.

None of these columns carry Evidence, and none are bound in
``EVIDENCE_FIELD_BY_VIEW_COLUMN``: they are derived or policy values, so a claim
about them is a ``derived`` claim with rule references, never a ``fact`` claim.
"""

from alembic import op

revision = "0009_performance_surface"
down_revision = "0008_cpu_catalog_integrated_gpu"
branch_labels = None
depends_on = None


PERFORMANCE_METRIC = """
SELECT e.entity_key, e.canonical_name AS name, h.category, h.form_factor,
       md.metric_key, md.label, md.canonical_unit AS unit, md.higher_is_better,
       COALESCE(bm.value_number, bm.value_integer::numeric) AS value,
       pr.protocol_key, pr.version AS protocol_version,
       br.run_key, br.test_date, br.run_origin,
       sd.source_key, br.raw_result_url AS source_url, br.environment
FROM truth.benchmark_subject s
JOIN truth.benchmark_run br ON br.id = s.run_id
JOIN truth.benchmark_protocol pr ON pr.id = br.protocol_id
JOIN truth.benchmark_metric bm ON bm.run_id = br.id
JOIN truth.metric_definition md ON md.id = bm.metric_id
JOIN truth.catalog_entity e ON e.id = s.entity_id
JOIN truth.hardware h ON h.entity_id = e.id
LEFT JOIN truth.source_document sd ON sd.id = br.source_document_id
WHERE s.is_primary
"""

# score_metric / alt_metric grade the part; rank_metric is the publisher's global
# rank, class_rank_metric its rank inside the same form-factor class.  A category
# with no class rank simply leaves that column NULL.
PERFORMANCE_RANKING = """
WITH score_def(category, score_metric, alt_metric, rank_metric, class_rank_metric) AS (
    SELECT * FROM (VALUES
        ('cpu'::text, 'passmark.cpu.mark'::text, 'passmark.cpu.single_thread'::text,
         'passmark.cpu.rank_multi'::text, 'passmark.cpu.rank_class'::text),
        ('gpu'::text, 'passmark.gpu.g3d_mark'::text, NULL::text,
         'passmark.gpu.rank'::text, NULL::text)
    ) AS t(category, score_metric, alt_metric, rank_metric, class_rank_metric)
),
measurement AS (
    SELECT entity_key, name, category, form_factor, metric_key, value, environment
    FROM agent_catalog.performance_metric
),
rolled AS (
    SELECT m.entity_key,
           max(m.name) AS name,
           max(m.category) AS category,
           max(m.form_factor) AS form_factor,
           max(CASE WHEN m.metric_key = d.score_metric THEN m.value END) AS score,
           max(CASE WHEN m.metric_key = d.alt_metric THEN m.value END) AS alt_score,
           max(CASE WHEN m.metric_key = d.rank_metric THEN m.value END) AS market_rank,
           max(CASE WHEN m.metric_key = d.class_rank_metric THEN m.value END) AS class_rank,
           max(coalesce(m.environment->>'population_all_cpus',
                        m.environment->>'population_all_gpus'))::numeric AS market_population,
           max(m.environment->>'population_in_class')::numeric AS class_population
    FROM measurement m
    JOIN score_def d ON d.category = m.category
    GROUP BY m.entity_key
),
positioned AS (
    SELECT entity_key, name, category, form_factor, score, alt_score,
           market_rank, market_population,
           CASE WHEN market_population > 0
                THEN round((100 * (1 - market_rank / market_population))::numeric, 1) END AS market_percentile,
           class_rank, class_population,
           CASE WHEN class_population > 0
                THEN round((100 * (1 - class_rank / class_population))::numeric, 1) END AS class_percentile,
           round((percent_rank() OVER (PARTITION BY category, form_factor ORDER BY score) * 100)::numeric, 1) AS catalogue_percentile,
           count(*) OVER (PARTITION BY category, form_factor) AS cohort_size,
           COALESCE(class_population, market_population) AS population_size
    FROM rolled
    WHERE score IS NOT NULL
),
-- Consecutive anchors form the bands the score is interpolated across.  The seed
-- data always includes a band at score 0 and a band above every observed score,
-- so every row lands in exactly one band.  The interval is half-open on the right:
-- a score that sits exactly on an anchor belongs to the band that *starts* there,
-- otherwise it would match two bands and the row would appear twice.
band AS (
    SELECT category, score AS score_lo, anchored_index AS index_lo,
           lead(score) OVER w AS score_hi,
           lead(anchored_index) OVER w AS index_hi
    FROM truth.performance_anchor
    WHERE active
    WINDOW w AS (PARTITION BY category ORDER BY score)
),
anchored AS (
    SELECT p.entity_key,
           greatest(least(
               b.index_lo + (p.score - b.score_lo) / (b.score_hi - b.score_lo) * (b.index_hi - b.index_lo),
               100), 0) AS index_100
    FROM positioned p
    JOIN band b ON b.category = p.category AND b.score_hi IS NOT NULL
                AND p.score >= b.score_lo AND p.score < b.score_hi
),
labelled AS (
    SELECT p.entity_key, p.name, p.category, p.form_factor, p.score, p.alt_score,
           p.market_rank, p.market_population, p.market_percentile,
           p.class_rank, p.class_population, p.class_percentile,
           round(a.index_100, 1) AS index_100,
           p.catalogue_percentile, p.population_size, p.cohort_size,
           CASE
               WHEN a.index_100 >= 90 THEN 'flagship'
               WHEN a.index_100 >= 70 THEN 'high'
               WHEN a.index_100 >= 45 THEN 'mid'
               WHEN a.index_100 >= 20 THEN 'entry'
               ELSE 'bottom'
           END AS tier_label
    FROM positioned p
    JOIN anchored a ON a.entity_key = p.entity_key
)
SELECT entity_key, name, category, form_factor, score, alt_score,
       market_rank, market_population, market_percentile,
       class_rank, class_population, class_percentile,
       index_100, catalogue_percentile, population_size, cohort_size, tier_label
FROM labelled
"""

BALANCE_RULE = """
SELECT rule_key, use_case, version, cpu_metric_key, gpu_metric_key,
       ratio_min, ratio_max, cpu_index_min, gpu_index_min,
       gpu_requirement, priority, rationale, active
FROM truth.balance_rule
WHERE active
"""

ENTITY_ALIAS = """
SELECT e.entity_key, e.canonical_name AS name, e.entity_type,
       a.alias, a.alias_type, a.language, a.normalized_alias
FROM truth.entity_alias a
JOIN truth.catalog_entity e ON e.id = a.entity_id
"""

# Exposed so ``test_v3_orm`` can check, across every migration rather than just the
# original one, that the agent read surface is exactly what the registry advertises.
VIEWS = {
    "performance_metric": PERFORMANCE_METRIC,
    "performance_ranking": PERFORMANCE_RANKING,
    "balance_rule_catalog": BALANCE_RULE,
    "entity_alias_catalog": ENTITY_ALIAS,
}


def upgrade() -> None:
    op.execute("""
        CREATE TABLE truth.balance_rule (
            id uuid NOT NULL,
            rule_key varchar(80) NOT NULL,
            use_case varchar(40) NOT NULL,
            version varchar(20) NOT NULL,
            cpu_metric_key varchar(80),
            gpu_metric_key varchar(80),
            ratio_min numeric(6,3),
            ratio_max numeric(6,3),
            cpu_index_min numeric(5,2),
            gpu_index_min numeric(5,2),
            gpu_requirement varchar(20) NOT NULL,
            priority varchar(24) NOT NULL,
            rationale text NOT NULL,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_balance_rule PRIMARY KEY (id),
            CONSTRAINT uq_balance_rule_key_version UNIQUE (rule_key, version),
            CONSTRAINT ck_balance_rule_gpu_requirement
                CHECK (gpu_requirement IN ('required','preferred','integrated_ok')),
            CONSTRAINT ck_balance_rule_priority
                CHECK (priority IN ('gpu_bound','cpu_bound','balanced')),
            CONSTRAINT ck_balance_rule_ratio_order
                CHECK (ratio_min IS NULL OR ratio_max IS NULL OR ratio_min <= ratio_max)
        )
    """)
    op.execute("CREATE INDEX ix_truth_balance_rule_use_case ON truth.balance_rule (use_case)")
    op.execute("""
        CREATE TABLE truth.performance_anchor (
            id uuid NOT NULL,
            category varchar(16) NOT NULL,
            score numeric NOT NULL,
            anchored_index numeric(5,2) NOT NULL,
            rationale text NOT NULL,
            version varchar(20) NOT NULL,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_performance_anchor PRIMARY KEY (id),
            CONSTRAINT uq_performance_anchor UNIQUE (category, version, score),
            CONSTRAINT ck_performance_anchor_category CHECK (category IN ('cpu','gpu')),
            CONSTRAINT ck_performance_anchor_index CHECK (anchored_index >= 0 AND anchored_index <= 100)
        )
    """)
    op.execute("CREATE INDEX ix_truth_performance_anchor_category ON truth.performance_anchor (category, version)")
    for name, statement in VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW agent_catalog.{name} AS {statement}")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS agent_catalog.entity_alias_catalog")
    op.execute("DROP VIEW IF EXISTS agent_catalog.balance_rule_catalog")
    op.execute("DROP VIEW IF EXISTS agent_catalog.performance_ranking")
    op.execute("DROP VIEW IF EXISTS agent_catalog.performance_metric")
    op.execute("DROP INDEX IF EXISTS truth.ix_truth_performance_anchor_category")
    op.execute("DROP TABLE IF EXISTS truth.performance_anchor")
    op.execute("DROP INDEX IF EXISTS truth.ix_truth_balance_rule_use_case")
    op.execute("DROP TABLE IF EXISTS truth.balance_rule")
