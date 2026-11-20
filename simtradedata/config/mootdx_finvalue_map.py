"""
FINVALUE ID to PTrade field mapping for mootdx financial data.

This file maps mootdx FINVALUE array indices to PTrade field names.
References: docs/reference/mootdx_api/docs/api/fields.md and
docs/reference/tdx_finvalue_extended.md
"""

# FINVALUE position -> (PTrade field name, description, source unit).
# Affair output converts wan_yuan into yuan; percent values retain percentage units.
# Note: FINVALUE data is 0-indexed array from the historical gpcw FINVALUE array
FINVALUE_TO_PTRADE = {
    # Report date (YYMMDD format, e.g., 150930 = 2015Q3)
    0: ("_report_date_raw", "Report period (YYMMDD)", None),

    # Per-share indicators
    1: ("basic_eps", "Basic EPS", "yuan"),
    2: ("_eps_deducted", "EPS after non-recurring", "yuan"),
    3: ("undivided_profit", "Undistributed profit per share", "yuan"),
    4: ("naps", "Net asset value per share", "yuan"),
    5: ("capital_surplus_fund_ps", "Capital reserve per share", "yuan"),
    6: ("roe", "Return on equity", "percent"),
    7: ("net_operate_cash_flow_ps", "Operating cash flow per share", "yuan"),

    # Balance sheet - key items
    8: ("cash_equivalents", "Cash and equivalents", "yuan"),
    11: ("account_receivable", "Accounts receivable", "yuan"),
    17: ("inventories", "Inventory", "yuan"),
    21: ("total_current_assets", "Total current assets", "yuan"),
    27: ("fixed_assets", "Fixed assets", "yuan"),
    33: ("intangible_assets", "Intangible assets", "yuan"),
    39: ("total_non_current_assets", "Total non-current assets", "yuan"),
    40: ("total_assets", "Total assets", "yuan"),
    41: ("shortterm_loan", "Short-term borrowings", "yuan"),
    44: ("accounts_payable", "Accounts payable", "yuan"),
    54: ("total_current_liability", "Total current liabilities", "yuan"),
    55: ("longterm_loan", "Long-term borrowings", "yuan"),
    62: ("total_non_current_liability", "Total non-current liabilities", "yuan"),
    63: ("total_liability", "Total liabilities", "yuan"),
    64: ("paidin_capital", "Paid-in capital (share capital)", "yuan"),
    68: ("retained_profit", "Undistributed profits", "yuan"),
    72: ("total_shareholder_equity", "Total shareholders' equity", "yuan"),

    # Income statement
    74: ("operating_revenue", "Operating revenue", "yuan"),
    75: ("operating_cost", "Operating cost", "yuan"),
    80: ("financial_expense", "Finance expenses", "yuan"),
    86: ("operating_profit", "Operating profit", "yuan"),
    92: ("total_profit", "Total profit", "yuan"),
    95: ("net_profit", "Net profit", "yuan"),
    96: ("np_parent_company_owners", "Net profit attributable to parent", "yuan"),

    # Cash flow statement
    107: ("net_operate_cash_flow", "Net cash from operations", "yuan"),
    119: ("net_invest_cash_flow", "Net cash from investing", "yuan"),
    128: ("net_finance_cash_flow", "Net cash from financing", "yuan"),

    # Solvency analysis
    159: ("current_ratio", "Current ratio", "ratio"),
    160: ("quick_ratio", "Quick ratio", "ratio"),
    162: ("interest_cover", "Interest coverage ratio", "ratio"),

    # Operating efficiency analysis
    172: ("accounts_receivables_turnover_rate", "A/R turnover rate", "times"),
    173: ("inventory_turnover_rate", "Inventory turnover rate", "times"),
    175: ("total_asset_turnover_rate", "Total asset turnover rate", "times"),
    179: ("current_assets_turnover_rate", "Current assets turnover rate", "times"),

    # Growth analysis
    183: ("operating_revenue_grow_rate", "Revenue YoY growth", "percent"),
    184: ("net_profit_grow_rate", "Net profit YoY growth", "percent"),
    185: ("net_asset_grow_rate", "Net asset YoY growth", "percent"),
    187: ("total_asset_grow_rate", "Total asset YoY growth", "percent"),

    # Profitability analysis
    197: ("_roe_calculated", "ROE calculation (not disclosed weighted ROE)", "percent"),
    199: ("net_profit_ratio", "Net profit margin", "percent"),
    200: ("roa", "ROA", "percent"),
    202: ("gross_income_ratio", "Gross profit margin", "percent"),

    # Capital structure
    210: ("_liability_to_asset", "Debt to asset ratio (not debt/equity)", "percent"),

    # Share capital
    238: ("total_shares", "Total shares", "shares"),
    239: ("a_floats", "Float A shares (listed circulating)", "shares"),
    242: ("_shareholder_count", "Number of shareholders", "count"),

    # TTM indicators
    276: ("_net_profit_ttm", "Net profit TTM", "yuan"),
    283: ("_operating_revenue_ttm", "Operating revenue TTM (10k yuan)", "wan_yuan"),

    # Announcement dates
    314: ("_publ_date_raw", "Publication date (YYMMDD)", None),
}

# Additional fields with direct PTrade statement/indicator counterparts.
FINVALUE_TO_PTRADE.update({
    9: ('trading_assets', '交易性金融资产', 'yuan'),
    10: ('bill_receivable', '应收票据', 'yuan'),
    12: ('advance_payment', '预付款项', 'yuan'),
    13: ('other_receivable', '其他应收款', 'yuan'),
    15: ('interest_receivable', '应收利息', 'yuan'),
    16: ('dividend_receivable', '应收股利', 'yuan'),
    19: ('non_current_asset_in_one_year', '一年内到期的非流动资产', 'yuan'),
    20: ('other_current_assets', '其他流动资产', 'yuan'),
    22: ('hold_for_sale_assets', '可供出售金融资产', 'yuan'),
    23: ('hold_to_maturity_investments', '持有至到期投资', 'yuan'),
    24: ('longterm_receivable_account', '长期应收款', 'yuan'),
    25: ('longterm_equity_invest', '长期股权投资', 'yuan'),
    26: ('investment_property', '投资性房地产', 'yuan'),
    28: ('constru_in_process', '在建工程', 'yuan'),
    29: ('construction_materials', '工程物资', 'yuan'),
    30: ('fixed_assets_liquidation', '固定资产清理', 'yuan'),
    31: ('biological_assets', '生产性生物资产', 'yuan'),
    32: ('oil_gas_assets', '油气资产', 'yuan'),
    34: ('development_expenditure', '开发支出', 'yuan'),
    35: ('good_will', '商誉', 'yuan'),
    36: ('long_deferred_expense', '长期待摊费用', 'yuan'),
    37: ('deferred_tax_assets', '递延所得税资产', 'yuan'),
    38: ('other_non_current_assets', '其他非流动资产', 'yuan'),
    42: ('trading_liability', '交易性金融负债', 'yuan'),
    43: ('notes_payable', '应付票据', 'yuan'),
    45: ('advance_receipts', '预收款项', 'yuan'),
    46: ('salaries_payable', '应付职工薪酬', 'yuan'),
    47: ('taxs_payable', '应交税费', 'yuan'),
    48: ('interest_payable', '应付利息', 'yuan'),
    49: ('dividend_payable', '应付股利', 'yuan'),
    50: ('other_payable', '其他应付款', 'yuan'),
    52: ('non_current_liability_in_one_year', '一年内到期的非流动负债', 'yuan'),
    53: ('other_current_liability', '其他流动负债', 'yuan'),
    56: ('bonds_payable', '应付债券', 'yuan'),
    57: ('longterm_account_payable', '长期应付款', 'yuan'),
    58: ('specific_account_payable', '专项应付款', 'yuan'),
    59: ('estimate_liability', '预计负债', 'yuan'),
    60: ('deferred_tax_liability', '递延所得税负债', 'yuan'),
    61: ('other_non_current_liability', '其他非流动负债', 'yuan'),
    65: ('capital_reserve_fund', '资本公积', 'yuan'),
    66: ('surplus_reserve_fund', '盈余公积', 'yuan'),
    67: ('treasury_stock', '减：库存股', 'yuan'),
    69: ('minority_interests', '少数股东权益', 'yuan'),
    70: ('foreign_currency_report_conv_diff', '外币报表折算价差', 'yuan'),
    73: ('total_liability_and_equity', '负债和所有者（或股东权益）合计', 'yuan'),
    76: ('operating_tax_surcharges', '营业税金及附加', 'yuan'),
    77: ('operating_expense', '销售费用', 'yuan'),
    78: ('administration_expense', '管理费用', 'yuan'),
    81: ('asset_impairment_loss', '资产减值损失', 'yuan'),
    82: ('fair_value_change_income', '加：公允价值变动净收益', 'yuan'),
    83: ('invest_income', '投资收益', 'yuan'),
    84: ('invest_income_associates', '其中：对联营企业和合营企业的投资收益', 'yuan'),
    88: ('non_operating_income', '营业外收入', 'yuan'),
    89: ('non_operating_expense', '减：营业外支出', 'yuan'),
    90: ('non_current_assetss_deal_loss', '其中：非流动资产处置净损失', 'yuan'),
    93: ('income_tax_cost', '减：所得税', 'yuan'),
    97: ('minority_profit', '少数股东损益', 'yuan'),
    98: ('goods_sale_service_render_cash', '销售商品、提供劳务收到的现金', 'yuan'),
    99: ('tax_levy_refund', '收到的税费返还', 'yuan'),
    100: ('other_cashin_related_operate', '收到其他与经营活动有关的现金', 'yuan'),
    101: ('subtotal_operate_cash_inflow', '经营活动现金流入小计', 'yuan'),
    102: ('goods_and_services_cash_paid', '购买商品、接受劳务支付的现金', 'yuan'),
    103: ('staff_behalf_paid', '支付给职工以及为职工支付的现金', 'yuan'),
    104: ('all_taxes_paid', '支付的各项税费', 'yuan'),
    105: ('other_operate_cash_paid', '支付其他与经营活动有关的现金', 'yuan'),
    106: ('subtotal_operate_cash_outflow', '经营活动现金流出小计', 'yuan'),
    108: ('invest_withdrawal_cash', '收回投资收到的现金', 'yuan'),
    109: ('invest_proceeds', '取得投资收益收到的现金', 'yuan'),
    110: ('fix_intan_other_asset_dispo_cash', '处置固定资产、无形资产和其他长期资产收回的现金净额', 'yuan'),
    111: ('net_cash_deal_sub_company', '处置子公司及其他营业单位收到的现金净额', 'yuan'),
    112: ('other_cash_from_invest_act', '收到其他与投资活动有关的现金', 'yuan'),
    113: ('subtotal_invest_cash_inflow', '投资活动现金流入小计', 'yuan'),
    114: ('fix_intan_other_asset_acqui_cash', '购建固定资产、无形资产和其他长期资产支付的现金', 'yuan'),
    115: ('invest_cash_paid', '投资支付的现金', 'yuan'),
    116: ('net_cash_from_sub_company', '取得子公司及其他营业单位支付的现金净额', 'yuan'),
    117: ('other_cash_to_invest_act', '支付其他与投资活动有关的现金', 'yuan'),
    118: ('subtotal_invest_cash_outflow', '投资活动现金流出小计', 'yuan'),
    120: ('cash_from_invest', '吸收投资收到的现金', 'yuan'),
    121: ('cash_from_borrowing', '取得借款收到的现金', 'yuan'),
    122: ('other_finance_act_cash', '收到其他与筹资活动有关的现金', 'yuan'),
    123: ('subtotal_finance_cash_inflow', '筹资活动现金流入小计', 'yuan'),
    124: ('borrowing_repayment', '偿还债务支付的现金', 'yuan'),
    125: ('dividend_interest_payment', '分配股利、利润或偿付利息支付的现金', 'yuan'),
    126: ('other_finance_act_payment', '支付其他与筹资活动有关的现金', 'yuan'),
    127: ('subtotal_finance_cash_outflow', '筹资活动现金流出小计', 'yuan'),
    129: ('exchan_rate_change_effect', '四、汇率变动对现金的影响', 'yuan'),
    131: ('cash_equivalent_increase', '五、现金及现金等价物净增加额', 'yuan'),
    132: ('begin_period_cash', '期初现金及现金等价物余额', 'yuan'),
    133: ('end_period_cash_equivalent', '期末现金及现金等价物余额', 'yuan'),
    135: ('assets_depreciation_reserves', '加：资产减值准备', 'yuan'),
    137: ('intangible_asset_amortization', '无形资产摊销', 'yuan'),
    138: ('deferred_expense_amort', '长期待摊费用摊销', 'yuan'),
    139: ('fix_intanther_asset_dispo_loss', '处置固定资产、无形资产和其他长期资产的损失', 'yuan'),
    140: ('fixed_asset_scrap_loss', '固定资产报废损失', 'yuan'),
    141: ('loss_from_fair_value_changes', '公允价值变动损失', 'yuan'),
    143: ('invest_loss', '投资损失', 'yuan'),
    144: ('defered_tax_asset_decrease', '递延所得税资产减少', 'yuan'),
    145: ('defered_tax_liability_increase', '递延所得税负债增加', 'yuan'),
    146: ('inventory_decrease', '存货的减少', 'yuan'),
    147: ('operate_receivable_decrease', '经营性应收项目的减少', 'yuan'),
    148: ('operate_payable_increase', '经营性应付项目的增加', 'yuan'),
    149: ('others', '其他', 'yuan'),
    150: ('net_operate_cash_flow_notes', '经营活动产生的现金流量净额2', 'yuan'),
    151: ('debt_to_captical', '债务转为资本', 'yuan'),
    152: ('cbs_expiring_within_one_year', '一年内到期的可转换公司债券', 'yuan'),
    153: ('fixed_assets_finance_leases', '融资租入固定资产', 'yuan'),
    154: ('cash_at_end_of_year', '现金的期末余额', 'yuan'),
    155: ('cash_at_beginning_of_year', '减：现金的期初余额', 'yuan'),
    156: ('cash_equivalents_at_end_of_year', '加：现金等价物的期末余额', 'yuan'),
    157: ('cash_equivalents_at_beginning', '减：现金等价物的期初余额', 'yuan'),
    158: ('net_incr_in_cash_and_equivalents', '现金及现金等价物净增加额', 'yuan'),
    176: ('fixed_asset_turnover_rate', '固定资产周转率(非金融类指标)', 'times'),
    177: ('accounts_receivables_turnover_days', '应收帐款周转天数(非金融类指标)', 'days'),
    178: ('inventory_turnover_days', '存货周转天数(非金融类指标)', 'days'),
    182: ('equity_turnover_rate', '股东权益周转率(非金融类指标)', 'times'),
    189: ('oper_profit_grow_rate', '营业利润增长率(%)', 'percent'),
    193: ('total_profit_cost_ratio', '成本费用利润率(%)', 'percent'),
    194: ('operating_profit_ratio', '营业利润率(非金融类指标)', 'percent'),
    206: ('net_profit_cut', '扣除非经常性损益后的净利润', 'yuan'),
    207: ('ebit', '息税前利润(EBIT)', 'yuan'),
    208: ('ebitda', '息税折旧摊销前利润(EBITDA)', 'yuan'),
    225: ('cash_flow_ps', '每股现金流量净额(元)', 'yuan'),
    268: ('ordinary_risk_reserve_fund', '一般风险准备(金融类)', 'yuan'),
    270: ('total_composite_income', '综合收益总额(利润表)', 'yuan'),
    271: ('se_without_mi', '归属于母公司股东权益(资产负债表)', 'yuan'),
    281: ('roe_weighted', '加权净资产收益率(每股指标)', 'percent'),
    298: ('other_composite_income', '其他综合收益(资产负债表)', 'yuan'),
    299: ('other_equityinstruments', '其他权益工具(资产负债表)', 'yuan'),
    321: ('enterprise_fcf_ps', '每股企业自由现金流', 'yuan'),
    322: ('shareholder_fcf_ps', '每股股东自由现金流', 'yuan'),
    401: ('specific_reserves', '专项储备(万元)', 'wan_yuan'),
    402: ('settlement_provi', '结算备付金(万元)', 'wan_yuan'),
    403: ('lend_capital', '拆出资金(万元)', 'wan_yuan'),
    405: ('derivative_assets', '衍生金融资产(万元)', 'wan_yuan'),
    406: ('insurance_receivables', '应收保费(万元)', 'wan_yuan'),
    407: ('reinsurance_receivables', '应收分保账款(万元)', 'wan_yuan'),
    409: ('bought_sellback_assets', '买入返售金融资产(万元)', 'wan_yuan'),
    412: ('borrowing_from_centralbank', '向中央银行借款(万元)', 'wan_yuan'),
    414: ('borrowing_capital', '拆入资金(万元)', 'wan_yuan'),
    415: ('derivative_liability', '衍生金融负债(万元)', 'wan_yuan'),
    416: ('sold_buyback_secu_proceeds', '卖出回购金融资产款(万元)', 'wan_yuan'),
    417: ('commission_payable', '应付手续费及佣金(万元)', 'wan_yuan'),
    418: ('reinsurance_payables', '应付分保账款(万元)', 'wan_yuan'),
    420: ('proxy_secu_proceeds', '代理买卖证券款(万元)', 'wan_yuan'),
    421: ('sub_issue_secu_proceeds', '代理承销证券款(万元)', 'wan_yuan'),
    427: ('long_salaries_pay', '长期应付职工薪酬(万元)', 'wan_yuan'),
    436: ('other_assets', '其他资产(万元)', 'wan_yuan'),
    501: ('diluted_eps', '稀释每股收益(元)', 'yuan'),
    502: ('total_operating_revenue', '营业总收入(万元)', 'wan_yuan'),
    503: ('exchange_income', '汇兑收益(万元)', 'wan_yuan'),
    504: ('ci_parent_company_owners', '其中:归属于母公司综合收益(万元)', 'wan_yuan'),
    505: ('ci_minority_owners', '其中:归属于少数股东综合收益(万元)', 'wan_yuan'),
    506: ('interest_income', '利息收入(万元)', 'wan_yuan'),
    507: ('premiums_earned', '已赚保费(万元)', 'wan_yuan'),
    508: ('commission_income', '手续费及佣金收入(万元)', 'wan_yuan'),
    509: ('interest_expense', '利息支出(万元)', 'wan_yuan'),
    510: ('commission_expense', '手续费及佣金支出(万元)', 'wan_yuan'),
    511: ('refunded_premiums', '退保金(万元)', 'wan_yuan'),
    514: ('policy_dividend_payout', '保单红利支出(万元)', 'wan_yuan'),
    515: ('reinsurance_cost', '分保费用(万元)', 'wan_yuan'),
    519: ('total_operating_cost', '营业总成本(万元)', 'wan_yuan'),
    562: ('net_deposit_increase', '客户存款和同业存放款项净增加额(万元)', 'wan_yuan'),
    563: ('net_borrowing_from_central_bank', '向中央银行借款净增加额(万元)', 'wan_yuan'),
    564: ('net_borrowing_from_finance_co', '向其他金融机构拆入资金净增加额(万元)', 'wan_yuan'),
    565: ('net_original_insurance_cash', '收到原保险合同保费取得的现金(万元)', 'wan_yuan'),
    566: ('net_reinsurance_cash', '收到再保险业务现金净额(万元)', 'wan_yuan'),
    567: ('net_insurer_deposit_investment', '保户储金及投资款净增加额(万元)', 'wan_yuan'),
    569: ('interest_and_commission_cashin', '收取利息、手续费及佣金的现金(万元)', 'wan_yuan'),
    571: ('net_buyback', '回购业务资金净增加额(万元)', 'wan_yuan'),
    572: ('net_loan_and_advance_increase', '客户贷款及垫款净增加额(万元)', 'wan_yuan'),
    573: ('net_deposit_in_cb_and_ib', '存放中央银行和同业款项净增加额(万元)', 'wan_yuan'),
    574: ('original_compensation_paid', '支付原保险合同赔付款项的现金(万元)', 'wan_yuan'),
    576: ('policy_dividend_cash_paid', '支付保单红利的现金(万元)', 'wan_yuan'),
})

# This PTrade ratio has no direct TDX position: liabilities / equity * 100.
DERIVED_FUNDAMENTAL_FIELDS = ["debt_equity_ratio"]

# Reverse mapping: PTrade field name -> FINVALUE position
PTRADE_TO_FINVALUE = {v[0]: k for k, v in FINVALUE_TO_PTRADE.items()}

# Core fields commonly used in analysis
CORE_FUNDAMENTAL_FIELDS = [
    # Per-share
    "basic_eps",
    "naps",
    "roe",

    # Growth
    "operating_revenue_grow_rate",
    "net_profit_grow_rate",

    # Profitability
    "net_profit_ratio",
    "gross_income_ratio",

    # Solvency
    "current_ratio",
    "quick_ratio",
    "debt_equity_ratio",

    # Efficiency
    "accounts_receivables_turnover_rate",
    "total_asset_turnover_rate",
    "interest_cover",

    # Share data
    "total_shares",
    "a_floats",
]

# All mapped FINVALUE fields that can be exported as user-facing financial data.
# Internal date helper fields are intentionally excluded.
FULL_FUNDAMENTAL_FIELDS = [
    name
    for name, _desc, _unit in FINVALUE_TO_PTRADE.values()
    if not name.startswith("_")
] + DERIVED_FUNDAMENTAL_FIELDS


def parse_finvalue_date(raw_date: int) -> str | None:
    """
    Parse FINVALUE date to ISO date string.

    Supports both formats:
    - YYMMDD (6-digit): e.g., 231231 -> '2023-12-31'
    - YYYYMMDD (8-digit): e.g., 20231231 -> '2023-12-31'

    Args:
        raw_date: Date in YYMMDD or YYYYMMDD format

    Returns:
        ISO date string (YYYY-MM-DD), or None if invalid
    """
    if not raw_date or raw_date == 0:
        return None

    raw_str = str(int(raw_date))

    if len(raw_str) == 8:
        # YYYYMMDD format
        return f"{raw_str[:4]}-{raw_str[4:6]}-{raw_str[6:8]}"

    # YYMMDD format (pad to 6 digits)
    raw_str = raw_str.zfill(6)
    year_prefix = "20" if int(raw_str[:2]) < 50 else "19"
    return f"{year_prefix}{raw_str[:2]}-{raw_str[2:4]}-{raw_str[4:6]}"
