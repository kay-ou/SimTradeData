# SimTradeData 数据同步规范

**版本**: v1.0
**日期**: 2025-10-02
**状态**: 正式发布

---

## 📋 目录

1. [数据验证规范](#数据验证规范)
2. [事务管理规范](#事务管理规范)
3. [批量导入规范](#批量导入规范)
4. [日志规范](#日志规范)
5. [错误处理规范](#错误处理规范)
6. [数据清理规范](#数据清理规范)

---

## 1. 数据验证规范

### 1.1 股票代码验证

#### 规则
- **必须过滤指数代码**,只处理真实股票
- 指数代码范围:
  - 上海指数: `000001-000999.SS`
  - 深圳指数: `399001-399999.SZ`

#### 实现示例
```python
def is_index_code(symbol: str) -> bool:
    """判断是否为指数代码"""
    code_part = symbol.split(".")[0]
    market_part = symbol.split(".")[-1] if "." in symbol else ""

    if code_part.isdigit() and len(code_part) == 6:
        code_num = int(code_part)

        # 上海指数: 000001-000999.SS
        if market_part == "SS" and 1 <= code_num <= 999:
            return True
        # 深圳指数: 399001-399999.SZ
        elif market_part == "SZ" and 399001 <= code_num <= 399999:
            return True

    return False
```

#### 应用场景
- ✅ 股票列表更新时过滤
- ✅ 数据库清理时删除
- ✅ 数据采集前验证

---

### 1.2 财务数据验证

#### 规则 (放宽原则)
- **至少有一个非零财务指标即认为有效**
- 允许负值 (如净利润可为负)
- 允许零值 (某些字段可能为0)

#### 实现示例
```python
def is_valid_financial_data(data: Dict[str, Any]) -> bool:
    """验证财务数据有效性"""
    if not data or not isinstance(data, dict):
        return False

    revenue = data.get("revenue", 0)
    net_profit = data.get("net_profit", 0)
    total_assets = data.get("total_assets", 0)
    shareholders_equity = data.get("shareholders_equity", 0)

    # 只要有一个非空/非零的财务指标就认为有效
    return (
        (revenue and revenue != 0)
        or (total_assets and total_assets != 0)
        or (shareholders_equity and shareholders_equity != 0)
        or (net_profit != 0)  # 净利润可以为负
    )
```

---

### 1.3 估值数据验证

#### 规则 (宽松原则)
- **任何一个估值指标非None即认为有效**
- 允许负数PE (亏损公司)
- 允许0值
- 检查所有估值指标: PE, PB, PS, PCF

#### 实现示例
```python
def is_valid_valuation_data(data: Dict[str, Any]) -> bool:
    """验证估值数据有效性"""
    if not data or not isinstance(data, dict):
        return False

    pe_ratio = data.get("pe_ratio")
    pb_ratio = data.get("pb_ratio")
    ps_ratio = data.get("ps_ratio")
    pcf_ratio = data.get("pcf_ratio")

    # 只要有任何一个估值指标存在且不为None就认为有效
    has_pe = pe_ratio is not None
    has_pb = pb_ratio is not None and pb_ratio != 0
    has_ps = ps_ratio is not None and ps_ratio != 0
    has_pcf = pcf_ratio is not None and pcf_ratio != 0

    return has_pe or has_pb or has_ps or has_pcf
```

#### 验证原则
- ❌ 不要: 要求必须为正数
- ❌ 不要: 设置严格的范围限制
- ✅ 应该: 只检查是否存在有效值
- ✅ 应该: 允许负数和零值

---

## 2. 事务管理规范

### 2.1 统一使用上下文管理器

#### 规则
- **禁止手动管理事务** (`BEGIN`/`COMMIT`/`ROLLBACK`)
- **必须使用** `db_manager.transaction()` 上下文管理器
- 事务内使用连接对象 `conn` 执行SQL

#### 正确示例
```python
# ✅ 正确: 使用上下文管理器
try:
    with self.db_manager.transaction() as conn:
        # 在事务内执行所有数据库操作
        conn.execute("INSERT INTO ...", params)
        conn.execute("UPDATE ...", params)
        # 事务自动提交
        return result
except Exception as e:
    # 事务自动回滚
    logger.error(f"操作失败: {e}")
    return error_result
```

#### 错误示例
```python
# ❌ 错误: 手动管理事务
try:
    self.db_manager.execute("BEGIN TRANSACTION")
    self.db_manager.execute("INSERT INTO ...", params)
    self.db_manager.execute("COMMIT")
except Exception as e:
    self.db_manager.execute("ROLLBACK")
```

### 2.2 事务粒度

#### 原则
- **单一操作**: 不需要事务
- **相关操作**: 使用一个事务
- **独立操作**: 分别使用不同事务

#### 示例
```python
# ✅ 单一操作,不需要事务
self.db_manager.execute("SELECT * FROM stocks")

# ✅ 相关操作,使用一个事务
with self.db_manager.transaction() as conn:
    conn.execute("INSERT INTO financials ...", params)
    conn.execute("UPDATE extended_sync_status ...", params)
```

---

## 3. 批量导入规范

### 3.1 数据类型清理

#### 规则
- **必须过滤非标量类型** (dict, list, tuple)
- 只保留基本类型 (int, float, str, bool, None)
- 记录被过滤的字段到DEBUG日志

#### 实现示例
```python
def clean_batch_data(raw_dict: dict) -> dict:
    """清理批量导入数据,移除非标量类型"""
    cleaned_dict = {}

    for key, value in raw_dict.items():
        # 只保留基本类型的值
        if not isinstance(value, (dict, list, tuple)):
            cleaned_dict[key] = value
        else:
            logger.debug(f"批量导入: 跳过非标量字段 {key} = {type(value)}")

    return cleaned_dict
```

### 3.2 DataFrame处理

#### 规则
- 批量导入返回的DataFrame可能是宽格式
- 某些列名可能是其他股票代码
- 必须清理后再使用

#### 处理流程
```python
# 1. 获取DataFrame
df = self._get_financial_data_for_period(filename)

# 2. 提取单只股票数据
stock_data = df.loc[symbol]

# 3. 转换为字典并清理
raw_dict = stock_data.to_dict()
cleaned_dict = clean_batch_data(raw_dict)  # 移除字典类型

# 4. 使用清理后的数据
record = {
    "symbol": symbol,
    "data": cleaned_dict
}
```

---

## 4. 日志规范

### 4.1 日志级别使用

#### 级别定义
- **DEBUG**: 调试信息,默认不显示
  - 数据验证详情
  - 字典类型字段过滤
  - 标准失败响应

- **INFO**: 正常运行信息
  - 阶段开始/完成
  - 数据统计
  - 性能指标

- **WARNING**: 需要关注但不影响运行
  - 数据源限流
  - 数据获取失败 (股票未上市等)

- **ERROR**: 错误,影响功能
  - 数据库操作失败
  - 事务回滚
  - 异常情况

#### 示例
```python
# DEBUG - 调试信息
self.logger.debug(f"财务数据包含字典类型: {value}，使用默认值")
self.logger.debug(f"数据源返回失败响应: {symbol} - {error_msg}")

# INFO - 正常信息
self.logger.info(f"✅ 批量导入完成: 获取到 {count} 只股票的财务数据")

# WARNING - 需要关注
self.logger.warning(f"数据获取失败: {symbol} (财务数据, 估值数据)")

# ERROR - 错误
self.logger.error(f"插入财务数据失败 {symbol}: {e}")
```

### 4.2 标准失败响应处理

#### 规则
- 数据源返回 `{'success': False, 'error': '...'}` 是**正常情况**
- 应记录为DEBUG级别,不是WARNING
- 只有异常的失败才记录WARNING

#### 实现
```python
if isinstance(raw_data, dict):
    if 'success' in raw_data and not raw_data.get('success'):
        # 标准失败响应 - DEBUG级别
        error_msg = raw_data.get('error', '未知错误')
        self.logger.debug(f"数据源返回失败响应: {symbol} - {error_msg}")
    else:
        # 异常情况 - WARNING级别
        self.logger.warning(f"未能处理的数据格式: {symbol}")
```

---

## 5. 错误处理规范

### 5.1 数值安全提取

#### 规则
- **必须处理异常类型**: None, dict, list, 空字符串
- **必须处理转换失败**: ValueError, TypeError
- **必须提供默认值**

#### 实现示例
```python
def safe_extract_numeric(value: Any, default: float = 0.0) -> float:
    """安全提取数值"""
    # 处理None或空字符串
    if value is None or value == "":
        return default

    # 处理字典类型
    if isinstance(value, dict):
        logger.debug(f"包含字典类型: {value}，使用默认值")
        return default

    # 处理列表类型
    if isinstance(value, (list, tuple)):
        logger.debug(f"包含列表类型: {value}，使用默认值")
        return default

    # 尝试转换
    try:
        return float(value)
    except (ValueError, TypeError):
        logger.debug(f"无法转换为数值: {value}，使用默认值")
        return default
```

### 5.2 使用统一错误处理装饰器

#### 规则
- 公共方法使用 `@unified_error_handler`
- 指定返回类型: `return_dict=True/False`
- 让装饰器处理异常和日志

#### 示例
```python
@unified_error_handler(return_dict=True)
def process_data(self, symbol: str) -> Dict[str, Any]:
    """处理数据"""
    # 装饰器会自动捕获异常并返回标准格式
    result = {"symbol": symbol, "success": True}
    # ... 业务逻辑
    return result
```

---

## 6. 数据清理规范

### 6.1 定期清理指数代码

#### SQL模板
```sql
-- 清理stocks表
DELETE FROM stocks
WHERE (CAST(SUBSTR(symbol, 1, 6) AS INTEGER) BETWEEN 1 AND 999 AND symbol LIKE '%.SS')
   OR (CAST(SUBSTR(symbol, 1, 6) AS INTEGER) BETWEEN 399001 AND 399999 AND symbol LIKE '%.SZ');

-- 清理market_data表
DELETE FROM market_data
WHERE symbol IN (
    SELECT symbol FROM market_data
    WHERE (CAST(SUBSTR(symbol, 1, 6) AS INTEGER) BETWEEN 1 AND 999 AND symbol LIKE '%.SS')
       OR (CAST(SUBSTR(symbol, 1, 6) AS INTEGER) BETWEEN 399001 AND 399999 AND symbol LIKE '%.SZ')
);

-- 清理financials表
DELETE FROM financials
WHERE symbol IN (
    SELECT symbol FROM financials
    WHERE (CAST(SUBSTR(symbol, 1, 6) AS INTEGER) BETWEEN 1 AND 999 AND symbol LIKE '%.SS')
       OR (CAST(SUBSTR(symbol, 1, 6) AS INTEGER) BETWEEN 399001 AND 399999 AND symbol LIKE '%.SZ')
);

-- 清理valuations表
DELETE FROM valuations
WHERE symbol IN (
    SELECT symbol FROM valuations
    WHERE (CAST(SUBSTR(symbol, 1, 6) AS INTEGER) BETWEEN 1 AND 999 AND symbol LIKE '%.SS')
       OR (CAST(SUBSTR(symbol, 1, 6) AS INTEGER) BETWEEN 399001 AND 399999 AND symbol LIKE '%.SZ')
);
```

### 6.2 清理时机

#### 建议
- **股票列表更新后**: 立即过滤指数代码
- **数据同步前**: 检查并清理
- **定期维护**: 每周/每月检查一次

---

## 7. 最佳实践总结

### 7.1 数据验证
- ✅ 放宽验证条件,提高数据采集率
- ✅ 允许负数和零值
- ✅ 检查所有相关指标
- ❌ 不要设置过于严格的范围限制

### 7.2 事务管理
- ✅ 使用上下文管理器
- ✅ 相关操作放在一个事务
- ❌ 不要手动BEGIN/COMMIT/ROLLBACK

### 7.3 批量导入
- ✅ 清理非标量类型
- ✅ 记录过滤信息到DEBUG
- ✅ 验证数据格式

### 7.4 日志管理
- ✅ 标准失败响应用DEBUG
- ✅ 真正的错误用WARNING/ERROR
- ✅ 重要信息用INFO

### 7.5 错误处理
- ✅ 安全提取数值
- ✅ 处理所有异常类型
- ✅ 提供合理默认值

---

## 8. 代码审查检查清单

在提交代码前,请检查:

- [ ] 是否过滤了指数代码?
- [ ] 数据验证是否过于严格?
- [ ] 是否使用事务上下文管理器?
- [ ] 批量导入数据是否清理了非标量类型?
- [ ] 日志级别是否合适?
- [ ] 是否使用安全数值提取函数?
- [ ] 是否处理了所有异常情况?

---

## 9. 常见问题

### Q1: 为什么要允许负数PE?
**A**: 亏损公司的PE为负数,这是有效的财务数据,不应该被过滤掉。

### Q2: 为什么不能手动管理事务?
**A**: 手动管理容易导致事务状态混乱,上下文管理器会自动处理提交和回滚。

### Q3: 批量导入为什么会有字典类型字段?
**A**: mootdx返回的DataFrame是宽格式,某些列名可能是其他股票代码,导致字典类型值。

### Q4: 什么时候应该记录WARNING?
**A**: 只有真正需要人工关注的异常情况才记录WARNING,标准的失败响应用DEBUG。

---

## 10. 版本历史

### v1.0 (2025-10-02)
- 初始版本
- 基于2025-10-02的错误修复经验制定
- 涵盖数据验证、事务管理、批量导入、日志、错误处理等规范

---

**维护者**: SimTradeData开发团队
**最后更新**: 2025-10-02
**状态**: 正式发布,强制执行
