"""
通信协议测试路由
"""
import asyncio
import json
from datetime import datetime
from fastapi.responses import HTMLResponse, Response as FastAPIResponse

def _build_protocol_report_html(session: ProtocolSession, logs: list, print_mode: bool) -> str:
    target = session.target or "未知设备"
    protocol = session.protocol or "未知协议"
    status_text = "已连接" if session.status == 1 else "已断开"
    
    script = ""
    if print_mode:
        script = """
<script>
  window.onload = () => {
    window.print();
    setTimeout(() => window.close(), 500);
  }
</script>
"""

    log_rows = ""
    for log in logs:
        dir_color = "#16a34a" if log.direction == "Rx" else ("#2563eb" if log.direction == "Tx" else "#64748b")
        log_rows += f"""
        <tr>
            <td>{log.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}</td>
            <td style="color: {dir_color}; font-weight: bold;">{log.direction}</td>
            <td>{log.frame_id or '-'}</td>
            <td>{log.dlc if log.dlc is not None else '-'}</td>
            <td>{log.data or '-'}</td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>通信协议验证报告_{session.id}</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif; padding: 24px; color: #0f172a; max-width: 1000px; margin: 0 auto; }}
    h1 {{ text-align: center; margin: 0 0 6px; }}
    .sub {{ text-align: center; color: #64748b; margin: 0 0 24px; font-size: 14px; }}
    .info-card {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 20px; border-radius: 8px; margin-bottom: 24px; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .info-item {{ display: flex; flex-direction: column; gap: 4px; }}
    .info-label {{ color: #64748b; font-size: 13px; text-transform: uppercase; }}
    .info-value {{ font-weight: 500; color: #0f172a; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px; }}
    th, td {{ border: 1px solid #e2e8f0; padding: 10px 12px; text-align: left; }}
    th {{ background: #f1f5f9; color: #475569; font-weight: 600; }}
    tr:nth-child(even) {{ background: #f8fafc; }}
    .footer {{ margin-top: 32px; text-align: right; color: #94a3b8; font-size: 12px; border-top: 1px solid #e2e8f0; padding-top: 16px; }}
    @media print {{
      @page {{ margin: 1cm; }}
      body {{ -webkit-print-color-adjust: exact; padding: 0; max-width: 100%; }}
      .info-card {{ background: #f8fafc !important; }}
      th {{ background: #f1f5f9 !important; }}
    }}
  </style>
</head>
<body>
  <h1>通信协议验证测试报告</h1>
  <p class="sub">报告编号：PR-{session.id}-{datetime.now().strftime('%Y%m%d')} | 测试人员：{session.executor or '-'}</p>
  
  <div class="info-card">
    <div class="info-item">
      <span class="info-label">测试对象 (Target)</span>
      <span class="info-value">{target}</span>
    </div>
    <div class="info-item">
      <span class="info-label">验证协议 (Protocol)</span>
      <span class="info-value" style="text-transform: uppercase;">{protocol}</span>
    </div>
    <div class="info-item">
      <span class="info-label">会话状态 (Status)</span>
      <span class="info-value">{status_text}</span>
    </div>
    <div class="info-item">
      <span class="info-label">统计数据 (Statistics)</span>
      <span class="info-value">发送 (Tx): {session.tx_count} | 接收 (Rx): {session.rx_count}</span>
    </div>
  </div>

  <h3 style="margin-bottom: 12px; color: #334155; border-left: 4px solid #3b82f6; padding-left: 8px;">通信日志记录</h3>
  <table>
    <thead>
      <tr>
        <th style="width: 180px;">时间戳 (Timestamp)</th>
        <th style="width: 80px;">方向 (Dir)</th>
        <th style="width: 120px;">标识/引脚 (ID/Pin)</th>
        <th style="width: 80px;">DLC/端口</th>
        <th>数据/状态 (Data/Status)</th>
      </tr>
    </thead>
    <tbody>
      {log_rows}
    </tbody>
  </table>

  <div class="footer">
    报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 程控安装部署系统
  </div>
  {script}
</body>
</html>"""


@router.get("/{session_id}/report/html")
async def download_protocol_report_html(
    session_id: int,
    print: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="测试记录不存在")
        
    logs = db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id).order_by(ProtocolLog.timestamp.asc()).all()
    html = _build_protocol_report_html(session, logs, bool(print))
    headers = {"Content-Disposition": f'attachment; filename="protocol_report_{session.id}.html"'}
    return HTMLResponse(content=html, headers=headers)

@router.get("/{session_id}/report/csv")
async def download_protocol_report_csv(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    import csv
    import io
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="测试记录不存在")
        
    logs = db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id).order_by(ProtocolLog.timestamp.asc()).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["时间戳", "方向", "标识/引脚", "DLC/端口", "数据/状态"])
    
    for log in logs:
        writer.writerow([
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            log.direction,
            log.frame_id or '',
            log.dlc if log.dlc is not None else '',
            log.data or ''
        ])
        
    headers = {"Content-Disposition": f'attachment; filename="protocol_report_{session.id}.csv"'}
    return FastAPIResponse(content=output.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from backend.utils.db import get_db
from backend.models.user import User
from backend.models import ProtocolSession, ProtocolLog
from backend.schemas import Response
from backend.routers.auth import get_current_user
from backend.utils.permission import require_permission

router = APIRouter()

class ConnectRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=200)
    protocol: str = Field(..., min_length=1, max_length=50)
    config: Optional[dict] = None


class SendRequest(BaseModel):
    frame_id: Optional[str] = None
    dlc: Optional[int] = None
    data: Optional[str] = None


def session_to_dict(s: ProtocolSession):
    return {
        "id": s.id,
        "target": s.target,
        "protocol": s.protocol,
        "config_json": s.config_json,
        "status": s.status,
        "tx": s.tx_count,
        "rx": s.rx_count,
        "executor": s.executor,
        "ip_address": s.ip_address,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def log_to_dict(l: ProtocolLog):
    return {
        "id": l.id,
        "timestamp": l.timestamp,
        "direction": l.direction,
        "frame_id": l.frame_id,
        "dlc": l.dlc,
        "data": l.data,
    }


@router.post("/connect", response_model=Response)
async def connect_device(
    payload: ConnectRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
    session = ProtocolSession(
        created_by_user_id=current_user.id,
        target=payload.target,
        protocol=payload.protocol,
        config_json=json.dumps(payload.config or {}, ensure_ascii=False),
        status=1,
        tx_count=0,
        rx_count=0,
        executor=current_user.username,
        ip_address=ip,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    sys_log = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="System",
        frame_id=None,
        dlc=None,
        data="已连接设备",
    )
    db.add(sys_log)
    db.commit()

    return {"code": 0, "message": "连接成功", "data": session_to_dict(session)}


@router.post("/{session_id}/disconnect", response_model=Response)
async def disconnect_device(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.status = 2
    db.commit()

    sys_log = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="System",
        frame_id=None,
        dlc=None,
        data="已断开连接",
    )
    db.add(sys_log)
    db.commit()
    return {"code": 0, "message": "断开成功"}


@router.post("/{session_id}/send", response_model=Response)
async def send_frame(
    session_id: int,
    payload: SendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.status != 1:
        raise HTTPException(status_code=400, detail="设备未连接")

    tx = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="Tx",
        frame_id=payload.frame_id,
        dlc=payload.dlc,
        data=payload.data,
    )
    db.add(tx)
    session.tx_count += 1
    db.commit()

    await asyncio.sleep(0.05)

    rx_data = "ACK" if (payload.data or "").strip() else "ACK"
    rx = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="Rx",
        frame_id=payload.frame_id,
        dlc=payload.dlc,
        data=rx_data,
    )
    db.add(rx)
    session.rx_count += 1
    db.commit()
    return {"code": 0, "message": "发送成功"}


@router.get("/{session_id}/logs", response_model=dict)
async def get_session_logs(
    session_id: int,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    query = db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id)
    total = query.count()
    logs = (
        query.order_by(ProtocolLog.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    logs.reverse()

    return {
        "code": 0,
        "message": "success",
        "data": [log_to_dict(l) for l in logs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "tx": session.tx_count,
        "rx": session.rx_count,
        "status": session.status,
    }


@router.post("/{session_id}/logs/clear", response_model=Response)
async def clear_session_logs(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id).delete()
    session.tx_count = 0
    session.rx_count = 0
    db.commit()
    return {"code": 0, "message": "清空成功"}


@router.get("/records", response_model=dict)
async def list_records(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    protocol: Optional[str] = None,
    executor: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    query = db.query(ProtocolSession)
    if keyword:
        query = query.filter(ProtocolSession.target.like(f"%{keyword}%"))
    if protocol:
        query = query.filter(ProtocolSession.protocol == protocol)
    if executor:
        query = query.filter(ProtocolSession.executor == executor)

    total = query.count()
    rows = (
        query.order_by(ProtocolSession.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "code": 0,
        "message": "success",
        "data": [session_to_dict(s) for s in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/records/{record_id}", response_model=Response)
async def get_record_detail(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == record_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"code": 0, "message": "success", "data": session_to_dict(session)}


@router.delete("/records/{record_id}", response_model=Response)
async def delete_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:delete")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == record_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.query(ProtocolLog).filter(ProtocolLog.session_id == record_id).delete()
    db.delete(session)
    db.commit()
    return {"code": 0, "message": "删除成功"}
