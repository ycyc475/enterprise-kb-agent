"""Typed office, training and hospitality calculations with source-backed caps."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_expenses"
down_revision = "0005_m5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversations", sa.Column("expense_context", JSONB(), nullable=False, server_default="{}")
    )
    op.add_column("calculations", sa.Column("expense_snapshot", JSONB()))
    op.add_column("fee_rules", sa.Column("application_cap", sa.Numeric(12, 2)))
    op.add_column(
        "fee_rules",
        sa.Column("reimbursement_ratio", sa.Numeric(6, 4), nullable=False, server_default="1"),
    )
    op.add_column(
        "fee_rules", sa.Column("max_quantity", sa.Integer(), nullable=False, server_default="100")
    )
    op.drop_constraint("ck_fee_rule_kind", "fee_rules", type_="check")
    op.drop_constraint("ck_fee_rule_grade", "fee_rules", type_="check")
    op.create_check_constraint(
        "ck_fee_rule_kind",
        "fee_rules",
        "kind IN ('lodging','daily','office','training','hospitality')",
    )
    op.create_check_constraint(
        "ck_fee_rule_grade",
        "fee_rules",
        "(kind='lodging' AND grade IS NOT NULL AND grade IN ('S1','S2')) OR (kind IN ('daily','office','training','hospitality') AND grade IS NULL)",
    )
    op.create_check_constraint(
        "ck_fee_rule_extended_amounts",
        "fee_rules",
        "reimbursement_ratio>0 AND reimbursement_ratio<=1 AND max_quantity>=1 AND max_quantity<=100 AND (application_cap IS NULL OR (application_cap>0 AND application_cap<=999999.99))",
    )


def downgrade():
    # Never silently discard extended policies or financial history.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM fee_rules WHERE kind IN ('office','training','hospitality')")
    ):
        raise RuntimeError(
            "Remove extended rules via an explicit reviewed migration before downgrade"
        )
    op.drop_constraint("ck_fee_rule_extended_amounts", "fee_rules", type_="check")
    op.drop_constraint("ck_fee_rule_kind", "fee_rules", type_="check")
    op.drop_constraint("ck_fee_rule_grade", "fee_rules", type_="check")
    op.create_check_constraint("ck_fee_rule_kind", "fee_rules", "kind IN ('lodging','daily')")
    op.create_check_constraint(
        "ck_fee_rule_grade",
        "fee_rules",
        "(kind='lodging' AND grade IS NOT NULL AND grade IN ('S1','S2')) OR (kind='daily' AND grade IS NULL)",
    )
    for field in ["application_cap", "reimbursement_ratio", "max_quantity"]:
        op.drop_column("fee_rules", field)
    op.drop_column("calculations", "expense_snapshot")
    op.drop_column("conversations", "expense_context")
