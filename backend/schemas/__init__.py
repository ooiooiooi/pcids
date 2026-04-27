"""
Pydantic 模式定义 - 用于请求/响应验证
"""
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List, Any
from datetime import datetime


# ============ 认证相关 ============

class Token(BaseModel):
    """JWT Token 响应"""
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Token 解析数据"""
    username: Optional[str] = None


class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1)


# ============ 用户相关 ============

class UserBase(BaseModel):
    """用户基础模式"""
    username: str = Field(..., min_length=1, max_length=50)
    email: Optional[str] = None
    role_id: Optional[int] = None


class UserCreate(UserBase):
    """创建用户请求"""
    password: str = Field(..., min_length=6)


class UserUpdate(BaseModel):
    """更新用户请求"""
    email: Optional[str] = None
    role_id: Optional[int] = None
    status: Optional[int] = None


class UserResponse(UserBase):
    """用户响应"""
    id: int
    status: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 角色相关 ============

class RoleBase(BaseModel):
    """角色基础模式"""
    name: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = None
    permission_ids: Optional[List[int]] = []


class RoleCreate(BaseModel):
    """创建角色请求"""
    name: str
    description: Optional[str] = None
    status: Optional[int] = 1
    data_scope: Optional[str] = "all"
    permission_ids: Optional[List[int]] = None


class RoleUpdate(BaseModel):
    """更新角色请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[int] = None
    data_scope: Optional[str] = None
    permission_ids: Optional[List[int]] = None


class RoleResponse(BaseModel):
    """角色响应"""
    id: int
    name: str
    description: Optional[str] = None
    permission_ids: List[int] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 产品相关 ============

class ProductBase(BaseModel):
    """产品基础模式"""
    name: str = Field(..., min_length=1, max_length=100)
    chip_type: str
    serial_number: Optional[str] = None
    voltage: Optional[str] = None
    temp_range: Optional[str] = None
    interface: Optional[str] = None
    config_description: Optional[str] = None
    usage_description: Optional[str] = None
    board_image: Optional[str] = None
    created_by: Optional[str] = None
    modified_by: Optional[str] = None


class ProductCreate(BaseModel):
    """创建产品请求"""
    name: str = Field(..., min_length=1, max_length=100)
    chip_type: str
    serial_number: str = Field(..., min_length=1, max_length=100)
    voltage: Optional[str] = None
    temp_range: Optional[str] = None
    interface: Optional[str] = None
    config_description: Optional[str] = None
    usage_description: Optional[str] = None
    board_image: str


class ProductUpdate(BaseModel):
    """更新产品请求"""
    name: Optional[str] = None
    chip_type: Optional[str] = None
    serial_number: Optional[str] = None
    voltage: Optional[str] = None
    temp_range: Optional[str] = None
    interface: Optional[str] = None
    config_description: Optional[str] = None
    usage_description: Optional[str] = None
    board_image: Optional[str] = None


class ProductResponse(ProductBase):
    """产品响应"""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 烧录器相关 ============

class BurnerBase(BaseModel):
    """烧录器基础模式"""
    name: str = Field(..., min_length=1, max_length=100)
    type: str
    sn: Optional[str] = None
    port: Optional[str] = None
    status: int = 0
    description: Optional[str] = None


class BurnerCreate(BurnerBase):
    """创建烧录器请求"""
    pass


class BurnerUpdate(BaseModel):
    """更新烧录器请求"""
    name: Optional[str] = None
    type: Optional[str] = None
    sn: Optional[str] = None
    port: Optional[str] = None
    status: Optional[int] = None
    description: Optional[str] = None


class BurnerResponse(BurnerBase):
    """烧录器响应"""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 脚本相关 ============

class ScriptBase(BaseModel):
    """脚本基础模式"""
    name: str = Field(..., min_length=1, max_length=100)
    type: str
    content: str
    ide_name: Optional[str] = None
    associated_board: Optional[str] = None
    associated_burner: Optional[str] = None
    modified_by: Optional[str] = None


class ScriptCreate(ScriptBase):
    """创建脚本请求"""
    pass


class ScriptUpdate(BaseModel):
    """更新脚本请求"""
    name: Optional[str] = None
    type: Optional[str] = None
    content: Optional[str] = None
    ide_name: Optional[str] = None
    associated_board: Optional[str] = None
    associated_burner: Optional[str] = None
    modified_by: Optional[str] = None


class ScriptResponse(ScriptBase):
    """脚本响应"""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 烧录任务相关 ============

class TaskBase(BaseModel):
    """任务基础模式"""
    software_name: str = Field(..., min_length=1, max_length=200)
    repository_id: Optional[int] = None
    executable: Optional[str] = None
    serial_number: Optional[str] = None
    board_name: Optional[str] = None
    target_ip: Optional[str] = None
    target_port: Optional[int] = None
    config_json: Optional[str] = None
    status: int = 0
    result: Optional[str] = None
    product_id: Optional[int] = None
    burner_id: Optional[int] = None
    keep_local: Optional[int] = None
    integrity: Optional[int] = None
    expected_checksum: Optional[str] = None
    current_md5: Optional[str] = None
    current_sha256: Optional[str] = None
    integrity_passed: Optional[int] = None
    version_check: Optional[int] = None
    history_checksum: Optional[str] = None
    consistency_passed: Optional[int] = None
    override_confirmed: Optional[int] = None


class TaskCreate(TaskBase):
    """创建任务请求"""
    pass


class TaskUpdate(BaseModel):
    """更新任务请求"""
    status: Optional[int] = None
    result: Optional[str] = None
    product_id: Optional[int] = None
    burner_id: Optional[int] = None
    keep_local: Optional[int] = None
    integrity: Optional[int] = None
    expected_checksum: Optional[str] = None
    version_check: Optional[int] = None
    history_checksum: Optional[str] = None
    override_confirmed: Optional[int] = None


class TaskResponse(TaskBase):
    """任务响应"""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 履历记录相关 ============

class RecordBase(BaseModel):
    """记录基础模式"""
    serial_number: Optional[str] = None
    software_name: str = Field(..., min_length=1, max_length=200)
    operator: Optional[str] = None
    ip_address: Optional[str] = None
    operation_time: datetime
    result: Optional[str] = None
    remark: Optional[str] = None
    log_data: Optional[str] = None


class RecordCreate(RecordBase):
    """创建记录请求"""
    pass


class RecordResponse(RecordBase):
    """记录响应"""
    id: int

    class Config:
        from_attributes = True


# ============ 制品仓库相关 ============

class RepositoryBase(BaseModel):
    """仓库基础模式"""
    name: str = Field(..., min_length=1, max_length=200)
    project_key: Optional[str] = None
    repo_id: Optional[str] = None
    tenant: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    file_url: Optional[str] = None
    size: Optional[int] = None
    md5: Optional[str] = None
    sha256: Optional[str] = None
    download_count: Optional[int] = None
    last_download_time: Optional[datetime] = None


class RepositoryCreate(RepositoryBase):
    """创建仓库请求"""
    pass


class RepositoryUpdate(BaseModel):
    """更新仓库请求"""
    name: Optional[str] = None
    repo_id: Optional[str] = None
    tenant: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    file_url: Optional[str] = None
    size: Optional[int] = None
    md5: Optional[str] = None
    sha256: Optional[str] = None


class RepositoryResponse(RepositoryBase):
    """仓库响应"""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 异常注入相关 ============

class InjectionBase(BaseModel):
    """注入基础模式"""
    type: str = Field(..., min_length=1, max_length=50)
    target: str = Field(..., min_length=1, max_length=200)
    config: Optional[str] = None
    status: int = 0


class InjectionCreate(InjectionBase):
    """创建注入请求"""
    pass


class InjectionUpdate(BaseModel):
    """更新注入请求"""
    type: Optional[str] = None
    target: Optional[str] = None
    config: Optional[str] = None
    status: Optional[int] = None
    result: Optional[str] = None


class InjectionResponse(InjectionBase):
    """注入响应"""
    id: int
    result: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 通信协议测试相关 ============

class ProtocolTestBase(BaseModel):
    """协议测试基础模式"""
    target: str = Field(..., min_length=1, max_length=200)
    address: Optional[str] = None
    data: Optional[str] = None


class ProtocolTestCreate(ProtocolTestBase):
    """创建协议测试请求"""
    pass


class ProtocolTestUpdate(BaseModel):
    """更新协议测试请求"""
    target: Optional[str] = None
    address: Optional[str] = None
    data: Optional[str] = None
    result: Optional[str] = None


class ProtocolTestResponse(ProtocolTestBase):
    """协议测试响应"""
    id: int
    result: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============ 通用响应 ============

class Response(BaseModel):
    """通用响应模式"""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    code: int = 0
    message: str = "success"
    data: Any = None


class PaginatedResponse(BaseModel):
    """分页响应模式"""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    code: int = 0
    message: str = "success"
    data: Any = None
    total: int = 0
    page: int = 1
    page_size: int = 10
