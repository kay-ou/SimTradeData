# PTrade 财务字段覆盖情况

本文对照 PTrade 文档中的字段，统计当前解析、存储和导出链路的支持情况。
字段名称与所属逻辑表以 [PTrade 财务字段定义](archive/requirements/Ptrade_Financial_API.md)为准。
统计表示字段支持率，不代表完整 PTrade 查询/API 等价，也不代表每家公司所有字段都有非空数据。

## 当前实现

- FINVALUE 已映射 **230 个位置**，提供 **223 个公开财务字段**，包含派生的负债权益比百分数。
- 明确标注为 `wan_yuan` 的金额转换为元；原本以元、每股、百分数及周转次数计量的字段保留原单位。
- 季度字段通过现有 `fundamentals/` 宽表写入和导出，没有为 PTrade 各逻辑表新增独立目录。
- 现有数据库自动补建可空列；已有历史记录需要显式刷新财务数据，才能填充新增字段。
- 不公开未知或保留的源位置。源数据中的零保持为零：通达信并不总能区分真实零值与不可用值。
- 源数据提供公告日期时予以保留；按报告期取得的快照不能证明它就是最初公告时的版本。

## 覆盖统计

统计不包含元数据字段。`valuation.a_shares` 当前特意保留为 NULL，不计为已提供数据。

| PTrade 逻辑表 | 已覆盖字段数 | 文档数据字段数 | 覆盖率 | 数据来源 |
| --- | ---: | ---: | ---: | --- |
| `valuation`（估值） | 11 | 19 | 58% | 日度估值来源及现有补充计算 |
| `balance_statement`（资产负债表） | 83 | 114 | 73% | 通达信 FINVALUE、现有 BaoStock/派生字段 |
| `income_statement`（利润表） | 35 | 54 | 65% | 通达信 FINVALUE、现有 BaoStock/派生字段 |
| `cashflow_statement`（现金流量表） | 69 | 81 | 85% | 通达信 FINVALUE、现有 BaoStock/派生字段 |
| `growth_ability`（成长能力） | 7 | 18 | 39% | 通达信 FINVALUE、现有 BaoStock/派生字段 |
| `profit_ability`（盈利能力） | 16 | 40 | 40% | 通达信 FINVALUE、现有 BaoStock/派生字段 |
| `eps`（每股指标） | 9 | 21 | 43% | 通达信 FINVALUE、现有 BaoStock/派生字段 |
| `operating_ability`（营运能力） | 8 | 11 | 73% | 通达信 FINVALUE、现有 BaoStock/派生字段 |
| `debt_paying_ability`（偿债能力） | 4 | 17 | 24% | 通达信 FINVALUE、现有 BaoStock/派生字段 |

## 字段口径修正与边界

- `debt_equity_ratio` 为负债 / 所有者权益 × 100。通达信由位置 63 和 72 派生；BaoStock 将小数形式的资产负债率换算为同一负债权益比百分数。权益分母为零时返回 NULL。
- `roa` 缺失时按 `roe / (1 + debt_equity_ratio / 100)` 派生；分母为零时保留 NULL，已有 ROA 不覆盖。历史错误派生值及相关 TTM 需单独重建。
- 披露的加权净资产收益率使用明确标注此口径的位置 281；位置 197 不作为披露的加权值。
- 营业总收入使用以万元计量的累计值位置 502，不使用单季度位置 312。
- 宽表的 `net_profit` 是利润表净利润总额（95）；盈利能力表同名字段要求归母净利润（96）。因此不将 `profit_ability.net_profit` 计为严格匹配。
- 利润表 `financial_expense`（80）不能代替现金流量表补充资料中的同名字段（142）。现金流量表的 `net_profit`、`minority_profit` 和 `financial_expense` 需要按所属表分别映射，暂不计入该表的严格覆盖。
- 源参考文档中 `non_operating_income` 的中文说明漏了“外”字；位置 88 对应标准的营业外收入。
- 部分新版通达信字段是合计项，无法分别填充 PTrade 的明细项，例如存款与同业存款合计、赔付净额、保险准备金合计、利息与手续费现金支出合计，以及固定资产/油气资产/生物资产折旧合计。这些值不作拆分或伪造。
- 新旧资产减值损失格式可能存在符号或口径差异；现有位置 81 为零时，不自动使用位置 521 替代。

## 已覆盖与缺失字段

### `valuation`

已覆盖： `a_floats`, `float_value`, `naps`, `pb`, `pcf`, `pe_ttm`, `ps_ttm`, `roe`, `total_shares`, `total_value`, `turnover_rate`.

缺失或与该表口径不严格等价： `a_shares`, `b_floats`, `b_shares`, `dividend_ratio`, `h_shares`, `pe_dynamic`, `pe_static`, `ps`.

### `balance_statement`

已覆盖： `account_receivable`, `accounts_payable`, `advance_payment`, `advance_receipts`, `bill_receivable`, `biological_assets`, `bonds_payable`, `borrowing_capital`, `borrowing_from_centralbank`, `bought_sellback_assets`, `capital_reserve_fund`, `cash_equivalents`, `commission_payable`, `constru_in_process`, `construction_materials`, `deferred_tax_assets`, `deferred_tax_liability`, `derivative_assets`, `derivative_liability`, `development_expenditure`, `dividend_payable`, `dividend_receivable`, `estimate_liability`, `fixed_assets`, `fixed_assets_liquidation`, `foreign_currency_report_conv_diff`, `good_will`, `hold_for_sale_assets`, `hold_to_maturity_investments`, `insurance_receivables`, `intangible_assets`, `interest_payable`, `interest_receivable`, `inventories`, `investment_property`, `lend_capital`, `long_deferred_expense`, `long_salaries_pay`, `longterm_account_payable`, `longterm_equity_invest`, `longterm_loan`, `longterm_receivable_account`, `minority_interests`, `non_current_asset_in_one_year`, `non_current_liability_in_one_year`, `notes_payable`, `oil_gas_assets`, `ordinary_risk_reserve_fund`, `other_assets`, `other_composite_income`, `other_current_assets`, `other_current_liability`, `other_equityinstruments`, `other_non_current_assets`, `other_non_current_liability`, `other_payable`, `other_receivable`, `paidin_capital`, `proxy_secu_proceeds`, `reinsurance_payables`, `reinsurance_receivables`, `retained_profit`, `salaries_payable`, `se_without_mi`, `settlement_provi`, `shortterm_loan`, `sold_buyback_secu_proceeds`, `specific_account_payable`, `specific_reserves`, `sub_issue_secu_proceeds`, `surplus_reserve_fund`, `taxs_payable`, `total_assets`, `total_current_assets`, `total_current_liability`, `total_liability`, `total_liability_and_equity`, `total_non_current_assets`, `total_non_current_liability`, `total_shareholder_equity`, `trading_assets`, `trading_liability`, `treasury_stock`.

缺失或与该表口径不严格等价： `advance_insurance`, `client_deposit`, `client_provi`, `compensation_payable`, `deposit`, `deposit_in_interbank`, `deposit_of_interbank`, `deposits_received`, `fixed_deposit`, `impawned_loan`, `independence_account_assets`, `independence_liability`, `insurer_deposit_investment`, `insurer_impawn_loan`, `life_insurance_reserve`, `loan_and_advance`, `long_defer_income`, `lt_health_insurance_lr`, `other_liability`, `outstanding_claim_reserve`, `policy_dividend_payable`, `r_metal`, `receivable_claims_r`, `receivable_life_r`, `receivable_lt_health_r`, `receivable_subrogation_fee`, `receivable_unearned_r`, `refundable_capital_deposit`, `refundable_deposit`, `seat_costs`, `unearned_premium_reserve`.

### `income_statement`

已覆盖： `administration_expense`, `asset_impairment_loss`, `basic_eps`, `ci_minority_owners`, `ci_parent_company_owners`, `commission_expense`, `commission_income`, `diluted_eps`, `exchange_income`, `fair_value_change_income`, `financial_expense`, `income_tax_cost`, `interest_expense`, `interest_income`, `invest_income`, `invest_income_associates`, `minority_profit`, `net_profit`, `non_current_assetss_deal_loss`, `non_operating_expense`, `non_operating_income`, `np_parent_company_owners`, `operating_cost`, `operating_expense`, `operating_profit`, `operating_revenue`, `operating_tax_surcharges`, `policy_dividend_payout`, `premiums_earned`, `refunded_premiums`, `reinsurance_cost`, `total_composite_income`, `total_operating_cost`, `total_operating_revenue`, `total_profit`.

缺失或与该表口径不严格等价： `amortization_expense`, `amortization_premium_reserve`, `amortization_reinsurance_cost`, `compensation_expense`, `insurance_commission_expense`, `net_commission_income`, `net_interest_income`, `net_proxy_secu_income`, `net_subissue_secu_income`, `net_trust_income`, `operating_payout`, `other_net_revenue`, `other_operating_cost`, `other_operating_revenue`, `premium_reserve`, `premiums_income`, `reinsurance`, `reinsurance_income`, `unearned_premium_reserve`.

### `cashflow_statement`

已覆盖： `all_taxes_paid`, `assets_depreciation_reserves`, `begin_period_cash`, `borrowing_repayment`, `cash_at_beginning_of_year`, `cash_at_end_of_year`, `cash_equivalent_increase`, `cash_equivalents_at_beginning`, `cash_equivalents_at_end_of_year`, `cash_from_borrowing`, `cash_from_invest`, `cbs_expiring_within_one_year`, `debt_to_captical`, `defered_tax_asset_decrease`, `defered_tax_liability_increase`, `deferred_expense_amort`, `dividend_interest_payment`, `end_period_cash_equivalent`, `exchan_rate_change_effect`, `fix_intan_other_asset_acqui_cash`, `fix_intan_other_asset_dispo_cash`, `fix_intanther_asset_dispo_loss`, `fixed_asset_scrap_loss`, `fixed_assets_finance_leases`, `goods_and_services_cash_paid`, `goods_sale_service_render_cash`, `intangible_asset_amortization`, `interest_and_commission_cashin`, `inventory_decrease`, `invest_cash_paid`, `invest_loss`, `invest_proceeds`, `invest_withdrawal_cash`, `loss_from_fair_value_changes`, `net_borrowing_from_central_bank`, `net_borrowing_from_finance_co`, `net_buyback`, `net_cash_deal_sub_company`, `net_cash_from_sub_company`, `net_deposit_in_cb_and_ib`, `net_deposit_increase`, `net_finance_cash_flow`, `net_incr_in_cash_and_equivalents`, `net_insurer_deposit_investment`, `net_invest_cash_flow`, `net_loan_and_advance_increase`, `net_operate_cash_flow`, `net_operate_cash_flow_notes`, `net_original_insurance_cash`, `net_reinsurance_cash`, `operate_payable_increase`, `operate_receivable_decrease`, `original_compensation_paid`, `other_cash_from_invest_act`, `other_cash_to_invest_act`, `other_cashin_related_operate`, `other_finance_act_cash`, `other_finance_act_payment`, `other_operate_cash_paid`, `others`, `policy_dividend_cash_paid`, `staff_behalf_paid`, `subtotal_finance_cash_inflow`, `subtotal_finance_cash_outflow`, `subtotal_invest_cash_inflow`, `subtotal_invest_cash_outflow`, `subtotal_operate_cash_inflow`, `subtotal_operate_cash_outflow`, `tax_levy_refund`.

缺失或与该表口径不严格等价： `accrued_expense_added`, `cash_from_bonds_issue`, `commission_cash_paid`, `deferred_expense_decreased`, `financial_expense`, `fixed_asset_depreciation`, `impawned_loan_net_increase`, `minority_profit`, `net_cash_for_reinsurance`, `net_deal_trading_assets`, `net_lend_capital`, `net_profit`.

### `growth_ability`

已覆盖： `basic_eps_yoy`, `net_asset_grow_rate`, `net_profit_grow_rate`, `np_parent_company_yoy`, `oper_profit_grow_rate`, `operating_revenue_grow_rate`, `total_asset_grow_rate`.

缺失或与该表口径不严格等价： `avg_np_yoy_past_five_year`, `diluted_eps_yoy`, `eps_grow_rate_ytd`, `naor_yoy`, `net_operate_cash_flow_yoy`, `np_parent_company_cut_yoy`, `oper_cash_ps_grow_rate`, `se_without_mi_grow_rate_ytd`, `sustainable_grow_rate`, `ta_grow_rate_ytd`, `total_profit_grow_rate`.

### `profit_ability`

已覆盖： `ebit`, `ebitda`, `gross_income_ratio`, `gross_income_ratio_ttm`, `net_profit_cut`, `net_profit_ratio`, `net_profit_ratio_ttm`, `operating_profit_ratio`, `roa`, `roa_ebit_ttm`, `roa_ttm`, `roe`, `roe_ttm`, `roe_weighted`, `roic`, `total_profit_cost_ratio`.

缺失或与该表口径不严格等价： `admini_expense_rate`, `admini_expense_rate_ttm`, `asset_impa_loss_to_tor`, `asset_impa_loss_to_tor_ttm`, `ebit_to_tor`, `ebit_to_tor_ttm`, `financial_expense_rate`, `financial_expense_rate_ttm`, `net_profit`, `np_to_tor`, `np_to_tor_ttm`, `operating_expense_rate`, `operating_expense_rate_ttm`, `operating_profit_to_tor`, `operating_profit_to_tor_ttm`, `period_costs_rate`, `period_costs_rate_ttm`, `roa_ebit`, `roe_avg`, `roe_cut`, `roe_cut_weighted`, `sales_cost_ratio`, `t_operating_cost_to_tor`, `t_operating_cost_to_tor_ttm`.

### `eps`

已覆盖： `basic_eps`, `capital_surplus_fund_ps`, `cash_flow_ps`, `diluted_eps`, `enterprise_fcf_ps`, `naps`, `net_operate_cash_flow_ps`, `shareholder_fcf_ps`, `undivided_profit`.

缺失或与该表口径不严格等价： `accumulation_fund_ps`, `cash_flow_ps_ttm`, `ebitps`, `eps`, `eps_ttm`, `main_income_ps`, `net_operate_cash_flow_ps_ttm`, `oper_profit_ps`, `operating_revenue_ps_ttm`, `retained_earnings_ps`, `surplus_reserve_fund_ps`, `total_operating_revenue_ps`.

### `operating_ability`

已覆盖： `accounts_receivables_turnover_days`, `accounts_receivables_turnover_rate`, `current_assets_turnover_rate`, `equity_turnover_rate`, `fixed_asset_turnover_rate`, `inventory_turnover_days`, `inventory_turnover_rate`, `total_asset_turnover_rate`.

缺失或与该表口径不严格等价： `accounts_payables_turnover_days`, `accounts_payables_turnover_rate`, `oper_cycle`.

### `debt_paying_ability`

已覆盖： `current_ratio`, `debt_equity_ratio`, `interest_cover`, `quick_ratio`.

缺失或与该表口径不严格等价： `debt_tangible_equity_ratio`, `ebitda_to_t_liability`, `long_debt_to_working_capital`, `nocf_to_current_liability`, `nocf_to_interest_bear_debt`, `nocf_to_net_debt`, `nocf_to_t_liability`, `opercashinto_current_debt`, `sewmi_to_interest_bear_debt`, `sewmi_to_total_liability`, `super_quick_ratio`, `tangible_a_to_interest_bear_debt`, `tangible_a_to_net_debt`.

## 刷新已有历史数据

数据库结构迁移只增加列，不会自动填充已完成下载的历史季度数据。
按所需日期范围使用现有财务刷新选项：

```bash
uv run python scripts/download_mootdx.py --start-date 2015-01-01 --refresh-fundamentals --skip-ohlcv
```

此下载脚本还会执行正常流程中其他非 OHLCV 阶段，默认写入 `data/cn.duckdb`。
该脚本目前没有 `--db-path` 参数；程序化调用 `MootdxDownloader` 时可传入 `db_path`。
刷新生产数据前核对目标数据库和日期范围。
新下载季度默认请求全部已映射公开字段。部分字段写入时保留未提供字段的已有值。

## 待完成事项

- 为 PTrade 各逻辑表及口径不同的同名字段补齐查询和返回语义。
- 用目标券商/版本的真实样本核验比率定义、源数据修订、累计与单季度口径，以及公告时点的数据可见性。
- 为剩余行业专用字段及只有合计值的财务字段补充外部财报来源。
- 日度市值、分红和估值指标需要带日期的行情与股本数据，不能将最新财务摘要前填至所有历史日期。
- 其他能力边界见 [通达信数据能力与接口缺口](PTRADE_DATA_COVERAGE.md)。
