# SimTradeData 脚本工具使用指南

**版本：** 1.0.0
**更新日期：** 2025-09-30

---

## 📁 脚本清单

| 脚本 | 用途 | 使用频率 | 重要性 |
|-----|------|---------|-------|
| `validate_schema.py` | Schema一致性验证 | 频繁 | ⭐⭐⭐ |
| `create_missing_indexes.sql` | 创建缺失索引 | 偶尔 | ⭐⭐ |
| `init_database.py` | 数据库初始化 | 偶尔 | ⭐⭐⭐ |

---

## 🔍 validate_schema.py

### 用途
验证数据库实际结构与 `schema.py` 定义是否一致。

### 使用场景
- ✅ 修改 `schema.py` 后验证
- ✅ 部署前检查数据库完整性
- ✅ 发现缺失的索引
- ✅ 数据库迁移后验证

### 使用方法

```bash
# 默认验证 data/simtradedata.db
poetry run python scripts/validate_schema.py

# 指定数据库路径
poetry run python scripts/validate_schema.py --db /path/to/database.db
```

### 输出示例

```
============================================================
SimTradeData Schema 一致性验证
============================================================

📋 检查表结构...
  ✅ 所有表都存在

🔍 检查索引...
  ❌ 缺失的索引:
     - idx_valuations_symbol_date
     - idx_valuations_date

📊 检查关键表字段...
  ✅ stocks 表字段完整
  ✅ market_data 表字段完整

📈 数据库统计信息:
  表数量: 12
  索引数量: 28
  stocks 记录数: 5,160
  market_data 记录数: 1,955,922

============================================================
❌ 发现 2 个问题，请检查上述输出。

修复建议：
  1. 运行索引创建脚本：
     sqlite3 data/simtradedata.db < scripts/create_missing_indexes.sql
============================================================
```

### 返回值
- `0` - 验证通过
- `1` - 发现问题

### CI/CD 集成

```yaml
# .github/workflows/test.yml
- name: Validate Database Schema
  run: |
    poetry run python scripts/init_database.py --db test.db
    poetry run python scripts/validate_schema.py --db test.db
```

---

## 🔧 create_missing_indexes.sql

### 用途
快速创建缺失的数据库索引。

### 使用场景
- ✅ `validate_schema.py` 报告缺失索引
- ✅ 性能优化时添加索引
- ✅ Schema 更新后补充索引
- ✅ 新环境初始化

### 使用方法

```bash
# 执行索引创建
sqlite3 data/simtradedata.db < scripts/create_missing_indexes.sql

# 查看执行结果（脚本自带验证）
# 会自动显示创建的索引列表
```

### 包含的索引

**valuations 表：**
- `idx_valuations_symbol_date` - 优化按股票和日期查询
- `idx_valuations_date` - 优化按日期排序
- `idx_valuations_created_at` - 优化最近数据查询

**data_source_quality 表：**
- `idx_data_quality_source` - 优化数据源质量查询
- `idx_data_quality_symbol` - 优化股票质量查询

### 输出示例

```
-- valuations 表索引 --
idx_valuations_created_at   valuations
idx_valuations_date         valuations
idx_valuations_symbol_date  valuations

-- data_source_quality 表索引 --
idx_data_quality_source     data_source_quality
idx_data_quality_symbol     data_source_quality

✅ 索引创建完成！
```

### 安全性
- 使用 `CREATE INDEX IF NOT EXISTS`，可重复执行
- 不会删除现有索引
- 不会修改数据

---

## 🏗️ init_database.py

### 用途
初始化数据库，创建所有表结构。

### 使用场景
- ✅ 新开发者环境搭建
- ✅ 测试环境创建
- ✅ 生产环境首次部署
- ✅ 数据库损坏后重建

### 使用方法

```bash
# 基础用法：创建数据库
poetry run python scripts/init_database.py --db data/simtradedata.db

# 验证模式：只检查不创建
poetry run python scripts/init_database.py --db data/simtradedata.db --validate-only

# 强制重建：删除旧数据库重新创建
poetry run python scripts/init_database.py --db data/simtradedata.db --force
```

### 参数说明

| 参数 | 说明 | 默认值 |
|-----|------|-------|
| `--db` | 数据库文件路径 | `data/simtradedata.db` |
| `--force` | 强制重建（删除现有数据库） | `False` |
| `--validate-only` | 仅验证，不创建 | `False` |

### 输出示例

```
🚀 初始化数据库: data/simtradedata.db
✅ 数据库初始化成功
📊 创建的表: 12 个
📊 创建的索引: 30 个
```

### ⚠️ 警告
- 使用 `--force` 会**删除所有现有数据**
- 建议在生产环境使用前先备份

---

## 🔄 典型工作流

### 1. 新环境搭建

```bash
# 步骤1：初始化数据库
poetry run python scripts/init_database.py

# 步骤2：验证数据库
poetry run python scripts/validate_schema.py

# 步骤3：运行测试
poetry run pytest tests/
```

### 2. Schema 更新后

```bash
# 步骤1：修改 schema.py
vim simtradedata/database/schema.py

# 步骤2：验证 schema
poetry run python scripts/validate_schema.py

# 步骤3：如有缺失索引，创建它们
sqlite3 data/simtradedata.db < scripts/create_missing_indexes.sql

# 步骤4：再次验证
poetry run python scripts/validate_schema.py
```

### 3. 性能优化

```bash
# 步骤1：发现慢查询
# （通过日志或性能监控）

# 步骤2：添加索引到 schema.py
vim simtradedata/database/schema.py

# 步骤3：在数据库中创建索引
# 方式1：使用脚本
sqlite3 data/simtradedata.db < scripts/create_missing_indexes.sql

# 方式2：手动创建
sqlite3 data/simtradedata.db "CREATE INDEX idx_xxx ON table_xxx(column)"

# 步骤4：验证索引创建
poetry run python scripts/validate_schema.py
```

### 4. 数据库迁移

```bash
# 步骤1：备份现有数据库
cp data/simtradedata.db data/simtradedata_backup_$(date +%Y%m%d).db

# 步骤2：运行迁移脚本（如果有）
# poetry run python scripts/migrate_xxx.py

# 步骤3：验证迁移结果
poetry run python scripts/validate_schema.py

# 步骤4：如有问题，恢复备份
# cp data/simtradedata_backup_20250930.db data/simtradedata.db
```

---

## 📊 故障排查

### 问题1：validate_schema.py 报告缺失索引

**解决方案：**
```bash
sqlite3 data/simtradedata.db < scripts/create_missing_indexes.sql
```

### 问题2：validate_schema.py 报告缺失表

**解决方案：**
```bash
# 重新初始化数据库（会丢失数据！）
poetry run python scripts/init_database.py --force

# 或者手动添加缺失的表
```

### 问题3：init_database.py 失败

**可能原因：**
- 数据库文件被锁定
- 权限不足
- 磁盘空间不足

**解决方案：**
```bash
# 检查文件权限
ls -l data/simtradedata.db

# 检查磁盘空间
df -h

# 检查是否有进程占用
lsof data/simtradedata.db
```

---

## 🎯 最佳实践

### 开发环境
- ✅ 每次修改 `schema.py` 后运行 `validate_schema.py`
- ✅ 提交代码前验证 schema 一致性
- ✅ 定期备份开发数据库

### 测试环境
- ✅ CI/CD 流程中集成 `validate_schema.py`
- ✅ 每次部署前验证数据库
- ✅ 使用独立的测试数据库

### 生产环境
- ✅ 部署前在暂存环境验证
- ✅ 执行数据库操作前先备份
- ✅ 使用 `--validate-only` 模式检查
- ✅ 避免使用 `--force` 选项

---

## 📝 维护指南

### 添加新脚本时

1. **命名规范**：使用描述性名称（如 `migrate_add_column.py`）
2. **文档注释**：在脚本顶部添加用途说明
3. **参数说明**：使用 `argparse` 提供清晰的参数
4. **更新本文档**：在脚本清单中添加新条目

### 更新现有脚本时

1. **向后兼容**：避免破坏现有用法
2. **版本说明**：在注释中记录变更
3. **测试验证**：确保在各环境正常工作

---

## 🔗 相关文档

- [开发者指南](../docs/DEVELOPER_GUIDE.md)
- [架构指南](../docs/Architecture_Guide.md)
- [生产部署指南](../docs/DEPLOYMENT.md)
- [数据库 Schema](../simtradedata/database/schema.py)

---

**最后更新：** 2025-09-30
**维护者：** SimTradeData 团队