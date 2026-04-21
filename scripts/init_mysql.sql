-- MySQL 初始化脚本
CREATE DATABASE IF NOT EXISTS rag_customer_service CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE rag_customer_service;

CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    intent VARCHAR(32),
    status ENUM('active','closed') DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP,
    summary TEXT,
    metadata JSON,
    INDEX idx_user_id (user_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL,
    role ENUM('user','assistant','system') NOT NULL,
    content TEXT NOT NULL,
    intent VARCHAR(32),
    sources JSON,
    tokens INT,
    latency_ms INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    INDEX idx_session_id (session_id),
    INDEX idx_role (role),
    INDEX idx_created_at (created_at)
);

CREATE TABLE IF NOT EXISTS documents (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    doc_type ENUM('product','policy','faq','manual') NOT NULL,
    title VARCHAR(255) NOT NULL,
    source_path VARCHAR(512) COMMENT '来源路径，用于增量摄取去重，如 products.json::SKU001',
    file_path VARCHAR(512),
    file_hash VARCHAR(64),
    file_mtime DATETIME COMMENT '文件修改时间，用于判断是否需要更新',
    chunk_count INT,
    status ENUM('pending','processing','done','failed','deleted') DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME ON UPDATE CURRENT_TIMESTAMP,
    indexed_at DATETIME,
    content_raw JSON COMMENT '原始文档内容，用于重索引',
    INDEX idx_doc_type (doc_type),
    INDEX idx_status (status),
    INDEX idx_source_path (source_path)
);

CREATE TABLE IF NOT EXISTS logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    level ENUM('info','warn','error') NOT NULL,
    service VARCHAR(64) NOT NULL,
    action VARCHAR(128) NOT NULL,
    request_id VARCHAR(36),
    detail JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_level (level),
    INDEX idx_service (service),
    INDEX idx_request_id (request_id),
    INDEX idx_created_at (created_at)
);

CREATE TABLE IF NOT EXISTS orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(32) NOT NULL UNIQUE,
    user_id VARCHAR(64) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    product_id VARCHAR(32),
    quantity INT DEFAULT 1,
    total_price DECIMAL(10,2) NOT NULL,
    status ENUM('pending','paid','shipped','delivered','cancelled','refunding','refunded') NOT NULL,
    payment_method VARCHAR(32),
    address VARCHAR(512),
    logistics_no VARCHAR(64),
    logistics_company VARCHAR(64),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    paid_at DATETIME,
    shipped_at DATETIME,
    delivered_at DATETIME,
    INDEX idx_user_id (user_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at),
    INDEX idx_logistics_no (logistics_no)
);

-- 测试数据（user_id='test-user'）
INSERT INTO orders (order_id, user_id, product_name, total_price, status, address, logistics_no, logistics_company, created_at, shipped_at) VALUES
('ORD20260001', 'test-user', 'iPhone 16 Pro Max 256GB 深空黑', 9999.00, 'shipped', '北京市朝阳区建国路88号SOHO现代城A座1201', 'SF1234567890', '顺丰速运', '2026-04-01 10:30:00', '2026-04-02 14:00:00'),
('ORD20260002', 'test-user', 'MacBook Pro 14寸 M3 Pro', 16999.00, 'shipped', '上海市浦东新区陆家嘴环路1000号', 'JD0012345678', '京东物流', '2026-03-28 15:20:00', '2026-03-29 09:30:00'),
('ORD20260003', 'test-user', 'AirPods Pro 2 代', 1899.00, 'delivered', '杭州市西湖区文三路398号', 'YT20260328001', '圆通速递', '2026-03-20 09:15:00', '2026-03-21 11:00:00'),
('ORD20260004', 'test-user', 'Sony WH-1000XM5', 2499.00, 'pending', '杭州市西湖区文三路398号', NULL, NULL, '2026-04-04 08:00:00', NULL)
ON DUPLICATE KEY UPDATE product_name=VALUES(product_name);
