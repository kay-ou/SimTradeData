# SimTradeData 生产环境部署指南

**版本**: 1.0
**更新日期**: 2025-09-30
**适用环境**: Linux生产服务器

---

## 📋 目录

1. [系统要求](#系统要求)
2. [安装步骤](#安装步骤)
3. [配置优化](#配置优化)
4. [性能调优](#性能调优)
5. [监控告警](#监控告警)
6. [备份恢复](#备份恢复)
7. [运维指南](#运维指南)
8. [故障排查](#故障排查)

---

## 系统要求

### 硬件要求

#### 最低配置
- **CPU**: 2核
- **内存**: 4GB
- **磁盘**: 50GB SSD
- **网络**: 10Mbps

#### 推荐配置
- **CPU**: 4核或更多
- **内存**: 8GB或更多
- **磁盘**: 100GB SSD (NVMe更佳)
- **网络**: 100Mbps或更快

### 软件要求

- **操作系统**: Ubuntu 20.04 LTS / CentOS 8 / Debian 11
- **Python**: 3.8+
- **Poetry**: 1.5+
- **systemd**: 用于服务管理
- **logrotate**: 日志轮转

---

## 安装步骤

### 1. 创建专用用户

```bash
# 创建simtradedata用户
sudo useradd -r -s /bin/bash -d /opt/simtradedata simtradedata

# 创建必要目录
sudo mkdir -p /opt/simtradedata
sudo mkdir -p /var/lib/simtradedata
sudo mkdir -p /var/log/simtradedata
sudo mkdir -p /var/backups/simtradedata

# 设置权限
sudo chown -R simtradedata:simtradedata /opt/simtradedata
sudo chown -R simtradedata:simtradedata /var/lib/simtradedata
sudo chown -R simtradedata:simtradedata /var/log/simtradedata
sudo chown -R simtradedata:simtradedata /var/backups/simtradedata
```

### 2. 安装Python和Poetry

```bash
# 安装Python 3.8+
sudo apt update
sudo apt install python3 python3-pip python3-venv -y

# 安装Poetry
curl -sSL https://install.python-poetry.org | python3 -

# 添加到PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### 3. 部署应用

```bash
# 切换到simtradedata用户
sudo su - simtradedata

# 克隆代码（或上传代码包）
cd /opt/simtradedata
git clone <repository-url> app
cd app

# 安装依赖
poetry install --no-dev

# 验证安装
poetry run python -c "import simtradedata; print('✅ 安装成功')"
```

### 4. 配置生产环境

```bash
# 复制生产配置
cp config.example.yaml config.yaml

# 编辑配置文件
nano config.yaml
```

**config.yaml示例**:

```yaml
# 生产环境配置
environment: production

# 使用生产配置
use_production_config: true

# 数据库配置
database:
  path: /var/lib/simtradedata/simtradedata.db

# 日志配置
logging:
  level: WARNING
  file_path: /var/log/simtradedata/simtradedata.log

# 监控配置
monitoring:
  enabled: true
  alert_enabled: true
```

### 5. 初始化数据库

```bash
# 初始化数据库结构
poetry run python -m simtradedata.cli init

# 验证数据库
sqlite3 /var/lib/simtradedata/simtradedata.db ".tables"
```

---

## 配置优化

### 数据库优化

SimTradeData使用SQLite，以下配置已在生产配置中优化：

```python
# 自动应用的SQLite PRAGMA
PRAGMA journal_mode = WAL;        # Write-Ahead Logging
PRAGMA synchronous = NORMAL;       # 平衡性能和安全
PRAGMA cache_size = -64000;        # 64MB缓存
PRAGMA temp_store = MEMORY;        # 内存临时存储
PRAGMA mmap_size = 268435456;      # 256MB内存映射
PRAGMA busy_timeout = 30000;       # 30秒繁忙超时
```

### 文件系统优化

```bash
# 使用noatime挂载（减少磁盘IO）
# 编辑 /etc/fstab
/dev/sda1 /var/lib/simtradedata ext4 defaults,noatime 0 2

# 重新挂载
sudo mount -o remount /var/lib/simtradedata
```

### 系统限制优化

```bash
# 编辑 /etc/security/limits.conf
simtradedata soft nofile 65536
simtradedata hard nofile 65536
simtradedata soft nproc 4096
simtradedata hard nproc 4096
```

---

## 性能调优

### 1. 数据同步优化

```yaml
# config.yaml
sync:
  max_concurrent_tasks: 3
  batch_size: 50
  enable_parallel_download: true
  max_download_workers: 3
```

**调优建议**:
- CPU核心数 >= 4: 设置 `max_concurrent_tasks: 4`
- 内存 >= 16GB: 设置 `batch_size: 100`
- 高速网络: 设置 `max_download_workers: 5`

### 2. 查询性能优化

```yaml
# config.yaml
query:
  cache_enabled: true
  cache_ttl_seconds: 600
  cache_max_size: 10000
  parallel_query_enabled: true
  max_parallel_queries: 4
```

### 3. 技术指标优化

技术指标已内置缓存和向量化计算：

- **缓存大小**: 默认5000项（生产环境）
- **缓存命中**: 434倍性能提升
- **批量计算**: 1.42ms/股

### 4. 内存管理

```yaml
# config.yaml
performance:
  max_memory_mb: 4096  # 根据服务器内存调整
  preload_hot_data: true
```

---

## 监控告警

### 1. 启用告警系统

```python
# 在应用启动脚本中
from simtradedata.monitoring import AlertSystem, AlertRuleFactory
from simtradedata.database import DatabaseManager

db_manager = DatabaseManager("/var/lib/simtradedata/simtradedata.db")
alert_system = AlertSystem(db_manager)

# 添加所有默认规则
rules = AlertRuleFactory.create_all_default_rules(db_manager)
for rule in rules:
    alert_system.add_rule(rule)

# 定期检查（可以用systemd timer或cron）
alerts = alert_system.check_all_rules()
```

### 2. 配置告警规则

默认启用的告警规则：

1. **数据质量检查** - 每60分钟
2. **同步失败检查** - 每30分钟
3. **数据库大小检查** - 每6小时
4. **数据缺失检查** - 每2小时
5. **陈旧数据检查** - 每4小时
6. **重复数据检查** - 每2小时

### 3. 告警通知

**方式1: 日志通知（默认）**

```bash
# 查看告警日志
tail -f /var/log/simtradedata/simtradedata.log | grep ERROR
```

**方式2: 邮件通知（自定义）**

```python
from simtradedata.monitoring import AlertNotifier

class EmailNotifier(AlertNotifier):
    def send(self, alert):
        # 发送邮件逻辑
        send_email(
            to="admin@example.com",
            subject=f"[{alert['severity']}] {alert['message']}",
            body=str(alert['details'])
        )
        return True

alert_system.add_notifier(EmailNotifier())
```

---

## 备份恢复

### 自动备份

生产配置已启用自动备份：

```yaml
database:
  backup_enabled: true
  backup_interval_hours: 12
  backup_path: /var/backups/simtradedata
```

### 手动备份

```bash
# 完整备份
sqlite3 /var/lib/simtradedata/simtradedata.db ".backup /var/backups/simtradedata/backup_$(date +%Y%m%d_%H%M%S).db"

# 压缩备份
tar -czf /var/backups/simtradedata/backup_$(date +%Y%m%d).tar.gz \
  /var/lib/simtradedata/simtradedata.db \
  /opt/simtradedata/app/config.yaml
```

### 恢复数据

```bash
# 停止服务
sudo systemctl stop simtradedata

# 恢复数据库
cp /var/backups/simtradedata/backup_YYYYMMDD_HHMMSS.db \
   /var/lib/simtradedata/simtradedata.db

# 验证数据库
sqlite3 /var/lib/simtradedata/simtradedata.db "PRAGMA integrity_check;"

# 启动服务
sudo systemctl start simtradedata
```

---

## 运维指南

### Systemd服务配置

创建 `/etc/systemd/system/simtradedata.service`:

```ini
[Unit]
Description=SimTradeData Service
After=network.target

[Service]
Type=simple
User=simtradedata
Group=simtradedata
WorkingDirectory=/opt/simtradedata/app
Environment="PATH=/opt/simtradedata/.local/bin:/usr/local/bin:/usr/bin"

# 启动命令（根据实际情况调整）
ExecStart=/opt/simtradedata/.local/bin/poetry run python -m simtradedata.cli serve

# 重启策略
Restart=on-failure
RestartSec=10s

# 资源限制
LimitNOFILE=65536
LimitNPROC=4096

# 日志
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**启用服务**:

```bash
# 重载systemd
sudo systemctl daemon-reload

# 启用开机自启
sudo systemctl enable simtradedata

# 启动服务
sudo systemctl start simtradedata

# 查看状态
sudo systemctl status simtradedata

# 查看日志
sudo journalctl -u simtradedata -f
```

### 定时任务（数据同步）

创建 `/etc/systemd/system/simtradedata-sync.service`:

```ini
[Unit]
Description=SimTradeData Daily Sync
After=network.target

[Service]
Type=oneshot
User=simtradedata
WorkingDirectory=/opt/simtradedata/app
ExecStart=/opt/simtradedata/.local/bin/poetry run python -m simtradedata.cli sync --incremental
```

创建 `/etc/systemd/system/simtradedata-sync.timer`:

```ini
[Unit]
Description=SimTradeData Daily Sync Timer

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

**启用定时任务**:

```bash
sudo systemctl enable simtradedata-sync.timer
sudo systemctl start simtradedata-sync.timer
sudo systemctl list-timers simtradedata-sync.timer
```

### 日志轮转

创建 `/etc/logrotate.d/simtradedata`:

```
/var/log/simtradedata/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 simtradedata simtradedata
    sharedscripts
    postrotate
        systemctl reload simtradedata > /dev/null 2>&1 || true
    endscript
}
```

### 监控脚本

创建 `/opt/simtradedata/scripts/health_check.sh`:

```bash
#!/bin/bash
# 健康检查脚本

LOG_FILE="/var/log/simtradedata/health_check.log"

# 检查服务状态
if systemctl is-active --quiet simtradedata; then
    echo "$(date): Service is running" >> $LOG_FILE
else
    echo "$(date): Service is DOWN!" >> $LOG_FILE
    systemctl restart simtradedata
fi

# 检查数据库
DB_CHECK=$(sqlite3 /var/lib/simtradedata/simtradedata.db "PRAGMA integrity_check;" 2>&1)
if [ "$DB_CHECK" != "ok" ]; then
    echo "$(date): Database integrity check FAILED: $DB_CHECK" >> $LOG_FILE
fi

# 检查磁盘空间
DISK_USAGE=$(df -h /var/lib/simtradedata | tail -1 | awk '{print $5}' | sed 's/%//')
if [ $DISK_USAGE -gt 80 ]; then
    echo "$(date): Disk usage is high: ${DISK_USAGE}%" >> $LOG_FILE
fi
```

**配置cron**:

```bash
# 每5分钟检查一次
*/5 * * * * /opt/simtradedata/scripts/health_check.sh
```

---

## 故障排查

### 常见问题

#### 1. 数据库锁定

**症状**: `database is locked` 错误

**解决方案**:

```bash
# 检查是否有进程持有锁
lsof /var/lib/simtradedata/simtradedata.db

# 如果确认安全，可以删除-wal和-shm文件
cd /var/lib/simtradedata
rm simtradedata.db-wal simtradedata.db-shm

# 重启服务
sudo systemctl restart simtradedata
```

#### 2. 内存不足

**症状**: 进程被OOM Killer终止

**解决方案**:

```yaml
# 减少并发任务
sync:
  max_concurrent_tasks: 2
  max_processing_workers: 4

# 减少缓存大小
query:
  cache_max_size: 5000

performance:
  max_memory_mb: 2048
```

#### 3. 数据同步失败

**症状**: 大量同步失败告警

**排查步骤**:

```bash
# 检查网络连接
ping -c 4 www.baidu.com

# 检查数据源状态
poetry run python -c "
from simtradedata.data_sources import DataSourceManager
from simtradedata.config import Config
manager = DataSourceManager(Config())
print(manager.check_all_sources())
"

# 查看详细错误日志
tail -100 /var/log/simtradedata/simtradedata.log | grep ERROR
```

#### 4. 性能下降

**排查步骤**:

```bash
# 检查慢查询
grep "slow query" /var/log/simtradedata/performance.log

# 数据库ANALYZE
sqlite3 /var/lib/simtradedata/simtradedata.db "ANALYZE;"

# 数据库VACUUM（定期维护）
sqlite3 /var/lib/simtradedata/simtradedata.db "VACUUM;"

# 检查磁盘IO
iostat -x 1 10
```

### 日志分析

```bash
# 查看错误日志
tail -f /var/log/simtradedata/error.log

# 查看性能日志
tail -f /var/log/simtradedata/performance.log

# 统计错误类型
grep ERROR /var/log/simtradedata/simtradedata.log | \
  awk -F': ' '{print $NF}' | \
  sort | uniq -c | sort -rn

# 查看慢查询Top 10
grep "slow query" /var/log/simtradedata/performance.log | \
  awk '{print $(NF-1), $NF}' | \
  sort -k2 -rn | \
  head -10
```

---

## 安全建议

### 1. 文件权限

```bash
# 数据库文件
chmod 600 /var/lib/simtradedata/simtradedata.db

# 配置文件
chmod 600 /opt/simtradedata/app/config.yaml

# 备份文件
chmod 600 /var/backups/simtradedata/*.db
```

### 2. 防火墙配置

```bash
# 如果开放API端口（例如8000）
sudo ufw allow from 192.168.1.0/24 to any port 8000

# 限制SSH访问
sudo ufw limit ssh
```

### 3. SELinux配置

```bash
# CentOS/RHEL
sudo semanage fcontext -a -t bin_t "/opt/simtradedata(/.*)?"
sudo restorecon -R /opt/simtradedata
```

---

## 性能基准

### 典型性能指标

- **查询响应**: < 50ms (平均)
- **并发查询**: 100+ QPS
- **数据同步**: 1-2秒/股票
- **技术指标计算**: 1.42ms/股票
- **缓存命中率**: > 85%

### 压力测试

```bash
# 安装ab工具
sudo apt install apache2-utils

# 测试查询性能
ab -n 1000 -c 10 http://localhost:8000/api/get_price?symbol=000001.SZ

# 查看结果
# Requests per second: XXX [#/sec]
# Time per request: XXX [ms]
```

---

## 升级指南

### 平滑升级步骤

```bash
# 1. 备份数据
sudo -u simtradedata sqlite3 /var/lib/simtradedata/simtradedata.db \
  ".backup /var/backups/simtradedata/before_upgrade_$(date +%Y%m%d).db"

# 2. 停止服务
sudo systemctl stop simtradedata

# 3. 更新代码
cd /opt/simtradedata/app
sudo -u simtradedata git pull
sudo -u simtradedata poetry install --no-dev

# 4. 数据库迁移（如需要）
sudo -u simtradedata poetry run python -m simtradedata.cli migrate

# 5. 启动服务
sudo systemctl start simtradedata

# 6. 验证
sudo systemctl status simtradedata
curl http://localhost:8000/health
```

---

## 附录

### A. 环境变量

```bash
# ~/.bashrc 或 /etc/environment
export SIMTRADEDATA_ENV=production
export SIMTRADEDATA_CONFIG=/opt/simtradedata/app/config.yaml
export SIMTRADEDATA_LOG_LEVEL=WARNING
```

### B. 监控指标

可通过API或CLI获取的监控指标：

- 数据库大小
- 数据记录数
- 查询QPS
- 缓存命中率
- 同步成功率
- 告警数量
- 系统资源使用

### C. 相关文档

- [架构指南](Architecture_Guide.md) - 完整的系统架构和设计
- [开发者指南](DEVELOPER_GUIDE.md) - 开发者扩展开发指南
- [CLI使用指南](CLI_USAGE_GUIDE.md) - 命令行工具使用说明
- [API参考文档](API_REFERENCE.md) - 完整的API接口文档
- [项目完成报告](archive/reports/PROJECT_COMPLETION_REPORT.md) - 历史归档

---

**部署支持**: 如遇问题，请查看日志或提交Issue。

*SimTradeData - 生产就绪的金融数据系统*