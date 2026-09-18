from sqlalchemy import text

from app.db.session import engine


def main() -> None:
    """Fail with a non-zero exit code when PostgreSQL cannot be reached."""
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1")).scalar_one()
    if result != 1:
        raise RuntimeError("Unexpected database connection test result")
    print("PostgreSQL connection successful.")


if __name__ == "__main__":
    main()
