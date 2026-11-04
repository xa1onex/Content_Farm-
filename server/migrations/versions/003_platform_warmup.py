"""Reserve each clip once per platform and start account warm-up on actual readiness."""
from alembic import op
import sqlalchemy as sa
revision='003_platform_warmup'
down_revision='002_identity_guards'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('accounts',sa.Column('warmup_enabled',sa.Boolean(),nullable=False,server_default=sa.true()))
    op.add_column('accounts',sa.Column('operational_started_at',sa.DateTime(timezone=True),nullable=True))
    op.create_unique_constraint('uq_account_platform_identity','accounts',['id','platform'])
    op.add_column('jobs',sa.Column('platform',sa.String(20),nullable=True))
    op.execute('UPDATE jobs SET platform=accounts.platform FROM accounts WHERE jobs.account_id=accounts.id')
    # Existing duplicates cause a transaction rollback, never an automatic deletion.
    op.alter_column('jobs','platform',nullable=False)
    op.create_foreign_key('fk_job_account_platform','jobs','accounts',['account_id','platform'],['id','platform'])
    op.create_unique_constraint('uq_clip_platform_once','jobs',['clip_id','platform'])

def downgrade():
    op.drop_constraint('uq_clip_platform_once','jobs',type_='unique')
    op.drop_constraint('fk_job_account_platform','jobs',type_='foreignkey')
    op.drop_column('jobs','platform')
    op.drop_constraint('uq_account_platform_identity','accounts',type_='unique')
    op.drop_column('accounts','operational_started_at')
    op.drop_column('accounts','warmup_enabled')
