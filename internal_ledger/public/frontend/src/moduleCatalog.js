const ALL_PRODUCT_ROLES = ['账套管理员', '账务查看者', '企业负责人', '财务主管', '会计', '出纳',
  '销售', '采购', '库存', '项目', '人事工资', '只读审计'];

export const MODULES = [
  {key: 'sales', label: '客户与销售', description: '管理客户、报价、订单和销售发票', icon: '🧾',
    route: '/app/selling', allowedRoles: ['账套管理员', '企业负责人', '销售'], tasks: [
      {label: '客户', description: '查看和维护客户资料', route: '/app/customer'},
      {label: '销售订单', description: '创建和跟进销售订单', route: '/app/sales-order'},
      {label: '销售发票', description: '开具和查询销售发票', route: '/app/sales-invoice'},
    ]},
  {key: 'purchase', label: '采购与供应商', description: '管理供应商、采购订单和采购发票', icon: '🛒',
    route: '/app/buying', allowedRoles: ['账套管理员', '企业负责人', '采购'], tasks: [
      {label: '供应商', description: '查看和维护供应商资料', route: '/app/supplier'},
      {label: '采购订单', description: '创建和跟进采购订单', route: '/app/purchase-order'},
      {label: '采购发票', description: '登记和查询采购发票', route: '/app/purchase-invoice'},
    ]},
  {key: 'stock', label: '商品与库存', description: '管理商品、仓库、出入库和库存余额', icon: '📦',
    route: '/app/stock', allowedRoles: ['账套管理员', '企业负责人', '采购', '库存'], tasks: [
      {label: '商品', description: '查看和维护商品档案', route: '/app/item'},
      {label: '库存出入库', description: '登记调拨、入库和出库', route: '/app/stock-entry'},
      {label: '库存余额', description: '查询各仓库实时库存', route: '/app/query-report/Stock%20Balance'},
    ]},
  {key: 'expense', label: '费用报销', description: '提交、审批和查询员工费用报销', icon: '💳',
    route: '/app/expense-claim', allowedRoles: ALL_PRODUCT_ROLES, tasks: [
      {label: '费用报销单', description: '查看和处理费用报销', route: '/app/expense-claim'},
      {label: '员工预支', description: '查看和处理员工预支', route: '/app/employee-advance'},
    ]},
  {key: 'crm', label: '客户关系', description: '管理线索、商机和客户跟进', icon: '🤝',
    route: '/app/crm', allowedRoles: ['账套管理员', '企业负责人', '销售'], tasks: [
      {label: '销售线索', description: '登记和跟进潜在客户', route: '/app/lead'},
      {label: '销售商机', description: '管理销售机会和阶段', route: '/app/opportunity'},
      {label: '客户', description: '查看正式客户资料', route: '/app/customer'},
    ]},
  {key: 'projects', label: '项目协作', description: '管理项目、任务和工时', icon: '📋',
    route: '/app/projects', allowedRoles: ['账套管理员', '企业负责人', '销售', '项目'], tasks: [
      {label: '项目', description: '查看项目进度和成本', route: '/app/project'},
      {label: '任务', description: '分配和跟进项目任务', route: '/app/task'},
      {label: '工时单', description: '登记项目工作时间', route: '/app/timesheet'},
    ]},
  {key: 'hr', label: '人事工资', description: '管理员工、考勤、请假和工资', icon: '👥',
    route: '/app/hr', allowedRoles: ['账套管理员', '企业负责人', '人事工资'], tasks: [
      {label: '员工', description: '查看和维护员工档案', route: '/app/employee'},
      {label: '考勤', description: '查看和登记员工考勤', route: '/app/attendance'},
      {label: '请假', description: '提交和审批请假申请', route: '/app/leave-application'},
      {label: '工资处理', description: '运行和查询工资单', route: '/app/payroll-entry'},
    ]},
];
