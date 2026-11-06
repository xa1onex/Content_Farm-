"""A deleted publication must never make its clip available again."""
from alembic import op
revision='004_permanent_clip_usage'
down_revision='003_platform_warmup'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('''
    CREATE TABLE clip_platform_uses (
      clip_id uuid NOT NULL, platform varchar(20) NOT NULL,
      sha256 varchar(64), job_id uuid NOT NULL,
      used_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (clip_id,platform),
      CONSTRAINT uq_used_hash_platform UNIQUE (sha256,platform)
    );
    INSERT INTO clip_platform_uses(clip_id,platform,sha256,job_id,used_at)
      SELECT j.clip_id,j.platform,c.sha256,j.id,j.created_at
      FROM jobs j JOIN clips c ON c.id=j.clip_id
      WHERE j.status='published' OR EXISTS(SELECT 1 FROM publications p WHERE p.job_id=j.id)
      ON CONFLICT DO NOTHING;

    CREATE FUNCTION remember_clip_platform_use() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME='jobs' THEN
        IF NEW.status='published' THEN
          INSERT INTO clip_platform_uses(clip_id,platform,sha256,job_id)
          SELECT NEW.clip_id,NEW.platform,c.sha256,NEW.id FROM clips c WHERE c.id=NEW.clip_id
          ON CONFLICT DO NOTHING;
        END IF;
      ELSE
        INSERT INTO clip_platform_uses(clip_id,platform,sha256,job_id)
        SELECT j.clip_id,j.platform,c.sha256,j.id FROM jobs j JOIN clips c ON c.id=j.clip_id WHERE j.id=NEW.job_id
        ON CONFLICT DO NOTHING;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER remember_job_clip_use AFTER INSERT OR UPDATE OF status ON jobs
      FOR EACH ROW EXECUTE FUNCTION remember_clip_platform_use();
    CREATE TRIGGER remember_publication_clip_use AFTER INSERT ON publications
      FOR EACH ROW EXECUTE FUNCTION remember_clip_platform_use();

    CREATE FUNCTION forbid_used_clip_job() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' OR NEW.status IN ('scheduled','retry_wait','assigned','processing') THEN
        IF EXISTS(SELECT 1 FROM clip_platform_uses u WHERE u.platform=NEW.platform AND
          (u.clip_id=NEW.clip_id OR u.sha256=(SELECT sha256 FROM clips WHERE id=NEW.clip_id))) THEN
          RAISE EXCEPTION 'Clip permanently used on this platform' USING ERRCODE='23505';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER no_used_clip_job BEFORE INSERT OR UPDATE OF status ON jobs
      FOR EACH ROW EXECUTE FUNCTION forbid_used_clip_job();
    CREATE FUNCTION forbid_clip_use_erasure() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'Platform usage is permanent' USING ERRCODE='23514';
    END $$;
    CREATE TRIGGER permanent_clip_use BEFORE UPDATE OR DELETE ON clip_platform_uses
      FOR EACH ROW EXECUTE FUNCTION forbid_clip_use_erasure();
    ''')

def downgrade():
    raise RuntimeError('Permanent clip usage cannot be erased by an automatic downgrade')
