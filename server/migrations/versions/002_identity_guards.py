"""Immutable passport and file identity, publication queue identity."""
from alembic import op
revision='002_identity_guards'
down_revision='45623cf05e54'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('CREATE SEQUENCE clip_passport_seq START WITH 1')
    op.execute('''CREATE FUNCTION guard_clip_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.passport IS DISTINCT FROM OLD.passport THEN
        RAISE EXCEPTION 'Passport is immutable' USING ERRCODE='23514';
      END IF;
      IF NEW.drive_file_id IS DISTINCT FROM OLD.drive_file_id THEN
        RAISE EXCEPTION 'Drive file identity is immutable; register a new clip' USING ERRCODE='23514';
      END IF;
      IF OLD.sha256 IS NOT NULL AND NEW.sha256 IS DISTINCT FROM OLD.sha256 THEN
        RAISE EXCEPTION 'Known content hash is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END; $$''')
    op.execute('CREATE TRIGGER clip_identity BEFORE UPDATE ON clips FOR EACH ROW EXECUTE FUNCTION guard_clip_identity()')
    op.execute('''CREATE FUNCTION guard_job_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.account_id IS DISTINCT FROM OLD.account_id OR NEW.clip_id IS DISTINCT FROM OLD.clip_id THEN
        RAISE EXCEPTION 'Job content/account identity is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END; $$''')
    op.execute('CREATE TRIGGER job_identity BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION guard_job_identity()')

def downgrade():
    op.execute('DROP TRIGGER job_identity ON jobs')
    op.execute('DROP FUNCTION guard_job_identity()')
    op.execute('DROP TRIGGER clip_identity ON clips')
    op.execute('DROP FUNCTION guard_clip_identity()')
    op.execute('DROP SEQUENCE clip_passport_seq')
