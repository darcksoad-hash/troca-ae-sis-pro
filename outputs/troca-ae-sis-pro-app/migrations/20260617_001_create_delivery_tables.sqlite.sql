BEGIN;

CREATE TABLE IF NOT EXISTS delivery_drivers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT,
  document TEXT,
  vehicle TEXT,
  plate TEXT,
  status TEXT NOT NULL DEFAULT 'Disponível',
  latitude REAL,
  longitude REAL,
  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS delivery_settings (
  id INTEGER PRIMARY KEY DEFAULT 1,
  price_per_km REAL NOT NULL DEFAULT 2.5,
  minimum_fee REAL NOT NULL DEFAULT 10,
  free_radius_km REAL NOT NULL DEFAULT 0,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS delivery_requests (
  id TEXT PRIMARY KEY,
  order_id TEXT REFERENCES service_orders(id),
  client_id TEXT REFERENCES clients(id),
  driver_id TEXT REFERENCES delivery_drivers(id),
  type TEXT NOT NULL DEFAULT 'Coleta',
  status TEXT NOT NULL DEFAULT 'Solicitada',
  priority TEXT NOT NULL DEFAULT 'Normal',
  scheduled_date TEXT,
  scheduled_time TEXT,
  address TEXT,
  pickup_latitude REAL,
  pickup_longitude REAL,
  distance_km REAL NOT NULL DEFAULT 0,
  freight_value REAL NOT NULL DEFAULT 0,
  assignment_status TEXT NOT NULL DEFAULT 'Manual',
  assignment_started_at TEXT,
  collected_at TEXT,
  delivered_at TEXT,
  document_status TEXT NOT NULL DEFAULT 'Pendente',
  document_notes TEXT,
  proof_url TEXT,
  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS delivery_locations (
  id TEXT PRIMARY KEY,
  delivery_id TEXT NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
  driver_id TEXT REFERENCES delivery_drivers(id),
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  accuracy_m REAL,
  status TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS delivery_events (
  id TEXT PRIMARY KEY,
  delivery_id TEXT NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
  user_id TEXT REFERENCES users(id),
  old_status TEXT,
  new_status TEXT NOT NULL,
  note TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS delivery_assignment_offers (
  id TEXT PRIMARY KEY,
  delivery_id TEXT NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
  driver_id TEXT NOT NULL REFERENCES delivery_drivers(id),
  status TEXT NOT NULL DEFAULT 'Oferecida',
  distance_km REAL NOT NULL DEFAULT 0,
  offered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NOT NULL,
  responded_at TEXT,
  note TEXT
);

CREATE INDEX IF NOT EXISTS idx_delivery_status ON delivery_requests(status);
CREATE INDEX IF NOT EXISTS idx_delivery_order ON delivery_requests(order_id);
CREATE INDEX IF NOT EXISTS idx_delivery_client ON delivery_requests(client_id);
CREATE INDEX IF NOT EXISTS idx_delivery_driver ON delivery_requests(driver_id);
CREATE INDEX IF NOT EXISTS idx_delivery_date ON delivery_requests(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_delivery_events_delivery ON delivery_events(delivery_id);
CREATE INDEX IF NOT EXISTS idx_delivery_events_user ON delivery_events(user_id);
CREATE INDEX IF NOT EXISTS idx_delivery_locations_delivery ON delivery_locations(delivery_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_delivery_locations_driver ON delivery_locations(driver_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_delivery_offers_delivery ON delivery_assignment_offers(delivery_id);
CREATE INDEX IF NOT EXISTS idx_delivery_offers_driver ON delivery_assignment_offers(driver_id);
CREATE INDEX IF NOT EXISTS idx_delivery_offers_status ON delivery_assignment_offers(status, expires_at);

INSERT OR IGNORE INTO delivery_settings(id, price_per_km, minimum_fee, free_radius_km, notes)
VALUES (1, 2.5, 10, 0, 'Configuracao inicial do modulo Coleta e Entrega.');

COMMIT;
