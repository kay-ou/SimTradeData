# 数据源能力评估总结

> 历史归档：本文描述当时的研究或测试结论，不作为当前实现与覆盖承诺。现行说明见[数据覆盖](../../PTRADE_DATA_COVERAGE.md)，操作入口见[文档导航](../../README.md)。

**文档类型**: 早期研究归档
**创建时间**: 2024-07-24
**最后更新**: 2025-09-30
**当前状态**: ⚠️ 已过时 - 仅供历史参考

---

## ⚠️ 重要提示

**本文档已过时**，当前架构已发生重大变化：

- ❌ **AkShare已移除** - 因稳定性问题被彻底替换
- ✅ **mootdx已成为主数据源** - 基于通达信，稳定可靠
- ✅ **BaoStock保留** - 作为辅助数据源
- ✅ **QStock保留** - 用于概念板块数据

**请参考最新文档**:
- [PTrade_API_Coverage_Analysis.md](./PTrade_API_Coverage_Analysis.md) - 最新覆盖分析
- [DataSource_mootdx_Analysis.md](./DataSource_mootdx_Analysis.md) - mootdx技术分析

---

## 📊 原始评估结论（已过时）

### 数据源支持度对比

| 数据源 | 原评估 | 实际采用 | 说明 |
|--------|--------|---------|------|
| **AkShare** | 85% | ❌ 已移除 | 稳定性差，已被mootdx替代 |
| **BaoStock** | 67% | ✅ 保留 | 辅助数据源，财务数据补充 |
| **QStock** | 56% | ✅ 保留 | 概念板块数据 |
| **mootdx** | - | ✅ 主力 | 新增，93%覆盖率 |

### 当前架构（2025-09-30）

```
主数据源: mootdx (93%覆盖率)
├─ 本地Reader模式（离线）
├─ 在线Quotes模式（实时）
└─ 322个FINVALUE财务字段

辅助数据源: BaoStock
├─ 6个财务查询接口
├─ 除权除息数据
└─ 交易日历

扩展数据源: QStock
└─ 概念板块数据
```

---

## 🔍 历史研究发现（归档）

以下内容为原始研究结论，仅作历史参考：

### BaoStock被低估的能力（已验证）

1. ✅ **股票状态检测** - 已集成
2. ✅ **估值数据** - 已使用
3. ✅ **行业板块信息** - 已集成
4. ✅ **分钟线数据** - 支持但mootdx更优
5. ✅ **财务数据** - 6个查询接口已使用

### QStock发现的能力（部分使用）

1. ✅ **概念板块数据** - 已集成使用
2. ⚠️ **实时行情** - 未使用（mootdx更稳定）
3. ⚠️ **资金流数据** - 未集成
4. ⚠️ **财务数据** - 未使用（mootdx/BaoStock足够）

---

## 📝 经验教训

### 为什么放弃AkShare？

1. **稳定性问题**
   - 频繁被限流封IP
   - 网络请求缓慢（5-30秒）
   - 接口变动频繁

2. **维护成本高**
   - 依赖第三方网站爬虫
   - 需要频繁适配接口变化
   - 错误处理复杂

3. **性能问题**
   - 网络请求延迟高
   - 无法离线使用
   - 批量请求效率低

### 为什么选择mootdx？

1. **稳定性优势**
   - 基于通达信官方协议
   - 不受第三方网站限制
   - 支持离线本地数据

2. **性能优势**
   - 本地读取毫秒级
   - 在线请求1-3秒
   - 批量处理高效

3. **功能完整性**
   - 322个FINVALUE财务字段
   - 100%深度行情覆盖
   - 日线/分钟线完整支持

---

## 🎯 最终实施方案

### 数据源优先级（当前）

```python
数据源优先级 = {
    '实时数据': ['mootdx'],
    '历史数据': ['mootdx', 'BaoStock'],
    '财务数据': ['mootdx', 'BaoStock'],
    '行业数据': ['mootdx', 'BaoStock'],
    '概念板块': ['QStock']
}
```

### 覆盖率对比

| 数据类型 | 原方案(AkShare) | 现方案(mootdx) | 提升 |
|---------|----------------|----------------|-----|
| 市场数据 | 100% | 100% | - |
| 财务数据 | 89% | 93% | +4% |
| 深度行情 | 0% | 100% | +100% |
| 稳定性 | ⭐⭐ | ⭐⭐⭐⭐⭐ | 大幅提升 |
| 性能 | ⭐⭐ | ⭐⭐⭐⭐⭐ | 10-100倍 |

---

## 📚 相关文档

### 当前有效文档
- [PTrade_API_Coverage_Analysis.md](./PTrade_API_Coverage_Analysis.md) - ⭐ 最新覆盖分析
- [DataSource_mootdx_Analysis.md](./DataSource_mootdx_Analysis.md) - ⭐ mootdx技术细节
- [DataSource_BaoStock_Analysis.md](./DataSource_BaoStock_Analysis.md) - BaoStock分析
- [DataSource_QStock_Analysis.md](./DataSource_QStock_Analysis.md) - QStock分析

### 归档文档（参考用）
- 本文档 - 早期研究结论
- Financial_DataSource_Capability_Analysis.md（原引用文件未保留：`./Financial_DataSource_Capability_Analysis.md`） - 基于AkShare的分析（已过时）

---

**归档日期**: 2025-09-30
**归档原因**: 架构已迁移至mootdx，AkShare相关评估不再适用
**维护状态**: 仅保留历史参考，不再更新
