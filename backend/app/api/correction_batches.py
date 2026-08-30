import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_writable_workspace
from app.services.rule_service import undo_correction_batch

router = APIRouter(prefix="/api/correction-batches", tags=["correction-batches"])


@router.post("/{batch_id}/undo")
async def undo_batch(
    batch_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    count = await undo_correction_batch(session, ctx.workspace.id, batch_id)
    if count < 0:
        raise HTTPException(status_code=404, detail="Correction batch not found")
    return {"batch_id": batch_id, "undone": count}
