# Neon PostgreSQL

The API uses SQLite by default for local development. To use Neon, install the
project dependencies (`uv sync`) and set `DATABASE_URL` to the connection string
copied from the Neon console. The application accepts Neon `postgresql://` URLs
and uses the psycopg 3 driver. Keep Neon SSL query parameters, such as
`sslmode=require`, intact.

## Render deployment

1. Create a Neon project and copy its pooled connection string from **Connect**
   in the Neon console.
2. In Render, open the API web service's **Environment** settings and add
   `DATABASE_URL` with the full Neon connection string. Do not commit this value.
3. Ensure the service also has the required PayPal and Gemini environment
   variables, and set `CORS_ORIGINS` to the deployed frontend origin.
4. Deploy the application. On startup, the API creates its tables using
   SQLAlchemy metadata.
5. Verify the service starts successfully and `/health` returns `{"ok":true}`.

For local development, copy `.env.example` to `.env` and leave its SQLite
`DATABASE_URL`, or set it to a Neon connection string. The `.env` file is
git-ignored.
