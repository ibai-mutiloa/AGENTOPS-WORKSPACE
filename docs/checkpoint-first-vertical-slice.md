# Checkpoint: first vertical slice

**Fecha:** 2026-11-13  
**Rama:** `feat/first-vertical-slice`

## Estado

Se ha implementado la primera vertical slice funcional del backend FastAPI para
un workflow de operaciones industriales multi-tenant:

- Autenticación con tokens JWT, contraseñas hash y control de membresías por
  organización.
- Modelo de datos y migración Alembic para organizaciones, usuarios,
  solicitudes, productos, compatibilidad, documentos, pedidos, propuestas,
  auditoría, invocaciones de herramientas y entregas simuladas.
- Seed determinista basado en [`fixtures/demo.json`](../fixtures/demo.json).
- Flujo completo de solicitud: intake, análisis determinista, recuperación de
  evidencia, borrador, edición versionada, aprobación, rechazo y entrega
  simulada idempotente.
- Aislamiento por tenant, permisos por rol, control de versiones e idempotencia
  de aprobación.
- Tratamiento explícito de información insuficiente, producto inexistente,
  compatibilidad no verificada, fallos de herramientas y fallos de entrega.
- Endpoints de autenticación, solicitudes, aprobaciones y entregas en
  [`apps/api/app/api/routes.py`](../apps/api/app/api/routes.py).
- Pruebas unitarias/integración de la vertical slice y una prueba específica de
  concurrencia PostgreSQL en
  [`apps/api/tests/test_vertical_slice.py`](../apps/api/tests/test_vertical_slice.py)
  y [`apps/api/tests/test_postgres_concurrency.py`](../apps/api/tests/test_postgres_concurrency.py).
- Workflow de CI actualizado para instalar dependencias y ejecutar las
  comprobaciones del backend.

## Validación realizada

Desde `apps/api`:

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

Resultado:

- `13 passed, 1 skipped`
- El test omitido requiere `TEST_DATABASE_URL` apuntando a una base PostgreSQL
  de pruebas dedicada (`*_test`).
- Ruff y mypy pasan sin errores.
- Pytest muestra únicamente el warning de deprecación de `httpx` emitido por
  `starlette.testclient`.

## Para retomar

1. Levantar PostgreSQL con `infra/docker-compose.yml` y ejecutar la migración
   Alembic.
2. Ejecutar el seed demo y verificar manualmente los endpoints en `/docs`.
3. Configurar `TEST_DATABASE_URL` y ejecutar la prueba de concurrencia real.
4. Revisar la siguiente iteración del modelo de dominio y conectar el frontend.
5. Mantener el adaptador de email como simulación hasta definir los límites de
   integración externa y aprobación humana.
