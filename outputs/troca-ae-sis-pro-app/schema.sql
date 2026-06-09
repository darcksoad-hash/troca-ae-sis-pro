PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS roles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  level INTEGER NOT NULL DEFAULT 0,
  permissions TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role_id TEXT NOT NULL REFERENCES roles(id),
  status TEXT NOT NULL DEFAULT 'Ativo',
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  last_login TEXT,
  password_changed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS company_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  system_name TEXT NOT NULL,
  trade_name TEXT NOT NULL,
  legal_name TEXT,
  document TEXT,
  phone TEXT,
  email TEXT,
  zip TEXT,
  street TEXT,
  number TEXT,
  neighborhood TEXT,
  city TEXT,
  state TEXT,
  logo_path TEXT,
  primary_color TEXT NOT NULL DEFAULT '#f9732f',
  dark_color TEXT NOT NULL DEFAULT '#18231f',
  theme TEXT NOT NULL DEFAULT 'light',
  warranty_term TEXT,
  print_footer TEXT
);

CREATE TABLE IF NOT EXISTS clients (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  document TEXT,
  zip TEXT,
  street TEXT,
  number TEXT,
  neighborhood TEXT,
  city TEXT,
  state TEXT,
  complement TEXT,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manufacturers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  support_phone TEXT,
  site TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS product_models (
  id TEXT PRIMARY KEY,
  manufacturer_id TEXT NOT NULL REFERENCES manufacturers(id),
  name TEXT NOT NULL,
  category TEXT,
  year INTEGER,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS suppliers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  document TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS parts (
  id TEXT PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  category TEXT,
  manufacturer_id TEXT REFERENCES manufacturers(id),
  compatible_models TEXT NOT NULL DEFAULT '[]',
  cost REAL NOT NULL DEFAULT 0,
  price REAL NOT NULL DEFAULT 0,
  stock INTEGER NOT NULL DEFAULT 0,
  min_stock INTEGER NOT NULL DEFAULT 0,
  supplier_id TEXT REFERENCES suppliers(id)
  ,warranty_days INTEGER NOT NULL DEFAULT 90
  ,usage_type TEXT NOT NULL DEFAULT 'Ambos'
);

CREATE TABLE IF NOT EXISTS purchase_entries (
  id TEXT PRIMARY KEY,
  supplier_id TEXT REFERENCES suppliers(id),
  date TEXT NOT NULL,
  document TEXT,
  status TEXT NOT NULL DEFAULT 'Recebido',
  notes TEXT,
  total REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS purchase_items (
  id TEXT PRIMARY KEY,
  purchase_id TEXT NOT NULL REFERENCES purchase_entries(id) ON DELETE CASCADE,
  part_id TEXT NOT NULL REFERENCES parts(id),
  quantity INTEGER NOT NULL DEFAULT 1,
  unit_cost REAL NOT NULL DEFAULT 0,
  lot TEXT
);

CREATE TABLE IF NOT EXISTS services (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT,
  labor REAL NOT NULL DEFAULT 0,
  warranty_days INTEGER NOT NULL DEFAULT 90,
  duration TEXT
);

CREATE TABLE IF NOT EXISTS service_orders (
  id TEXT PRIMARY KEY,
  number INTEGER NOT NULL UNIQUE,
  client_id TEXT NOT NULL REFERENCES clients(id),
  device_manufacturer_id TEXT REFERENCES manufacturers(id),
  device_model_id TEXT REFERENCES product_models(id),
  device_brand TEXT,
  device_model TEXT,
  device_imei TEXT,
  device_color TEXT,
  device_password TEXT,
  device_condition TEXT,
  status TEXT NOT NULL DEFAULT 'Aberta',
  approval_status TEXT NOT NULL DEFAULT 'Pendente',
  priority TEXT NOT NULL DEFAULT 'Normal',
  opened TEXT NOT NULL,
  due TEXT,
  technician_id TEXT REFERENCES users(id),
  technician_name TEXT,
  defect TEXT,
  diagnosis TEXT,
  solution TEXT,
  photos_notes TEXT,
  warranty_term TEXT,
  delivery_term TEXT,
  follow_up TEXT,
  approval_signature TEXT,
  approval_signed_at TEXT,
  delivery_signature TEXT,
  delivery_signed_at TEXT,
  discount REAL NOT NULL DEFAULT 0,
  paid REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_parts (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES service_orders(id) ON DELETE CASCADE,
  part_id TEXT NOT NULL REFERENCES parts(id),
  quantity INTEGER NOT NULL DEFAULT 1,
  unit_price REAL NOT NULL DEFAULT 0,
  unit_cost REAL NOT NULL DEFAULT 0,
  warranty_days INTEGER NOT NULL DEFAULT 90
);

CREATE TABLE IF NOT EXISTS order_services (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES service_orders(id) ON DELETE CASCADE,
  service_id TEXT NOT NULL REFERENCES services(id),
  labor REAL NOT NULL DEFAULT 0,
  warranty_days INTEGER NOT NULL DEFAULT 90
);

CREATE TABLE IF NOT EXISTS order_status_history (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES service_orders(id) ON DELETE CASCADE,
  user_id TEXT REFERENCES users(id),
  old_status TEXT,
  new_status TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_photos (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES service_orders(id) ON DELETE CASCADE,
  file_name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS finance_entries (
  id TEXT PRIMARY KEY,
  order_id TEXT REFERENCES service_orders(id),
  cash_session_id TEXT,
  recurrence_id TEXT,
  type TEXT NOT NULL,
  date TEXT NOT NULL,
  due_date TEXT,
  category TEXT,
  description TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  payment_method TEXT,
  card_fee REAL NOT NULL DEFAULT 0,
  reconciled INTEGER NOT NULL DEFAULT 0,
  reconciled_at TEXT,
  recurrence_frequency TEXT,
  recurrence_until TEXT,
  installment INTEGER NOT NULL DEFAULT 1,
  installments INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cash_sessions (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL UNIQUE,
  opening_amount REAL NOT NULL DEFAULT 0,
  closing_amount REAL NOT NULL DEFAULT 0,
  expected_amount REAL NOT NULL DEFAULT 0,
  difference REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'Aberto',
  opened_by TEXT REFERENCES users(id),
  closed_by TEXT REFERENCES users(id),
  opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  closed_at TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS stock_movements (
  id TEXT PRIMARY KEY,
  part_id TEXT NOT NULL REFERENCES parts(id),
  order_id TEXT REFERENCES service_orders(id),
  type TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  unit_cost REAL NOT NULL DEFAULT 0,
  supplier_id TEXT REFERENCES suppliers(id),
  lot TEXT,
  purchase_id TEXT REFERENCES purchase_entries(id),
  sale_id TEXT,
  reason TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pos_sales (
  id TEXT PRIMARY KEY,
  number INTEGER NOT NULL UNIQUE,
  client_id TEXT REFERENCES clients(id),
  date TEXT NOT NULL,
  payment_method TEXT,
  discount REAL NOT NULL DEFAULT 0,
  total REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'Recebido',
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pos_sale_items (
  id TEXT PRIMARY KEY,
  sale_id TEXT NOT NULL REFERENCES pos_sales(id) ON DELETE CASCADE,
  part_id TEXT NOT NULL REFERENCES parts(id),
  quantity INTEGER NOT NULL DEFAULT 1,
  unit_price REAL NOT NULL DEFAULT 0,
  unit_cost REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id),
  action TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
