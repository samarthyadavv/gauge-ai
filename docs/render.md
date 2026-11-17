# Deploy the backend to Render

Deploy the root `render.yaml` as a Render Blueprint. Enter the exact HTTPS origin of your frontend as `BASE_URL`, without a trailing slash. Render generates `ADMIN_KEY` and connects the service to Postgres over its internal network. Retrieve the operator key from the web service's Environment tab when you need to sign in.

The container applies migrations before starting the API and worker. Both processes share one free web instance and restart together. Keep the instance count at one for this setup. Interrupted in-flight evaluations are retained as partial results and can be rerun explicitly.

Set `GAUGE_API_ORIGIN` in the frontend's Netlify build environment to the Render web service's HTTPS origin. Redeploy the frontend, then sign in and run a mock evaluation to check the connection. Use `/ready` on the Render service to check API and database readiness.

The free web instance sleeps after 15 minutes of inactivity and may restart. Free Render Postgres expires 30 days after creation. Move data to a durable database before expiry if you need to keep runs or datasets.
