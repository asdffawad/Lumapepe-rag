-- ============================================================
-- 商品SKU解析与检索模块 - 数据库初始化脚本
-- ============================================================
-- 执行前请确认已创建数据库: CREATE DATABASE rag_customer_service CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- ============================================================

-- ----------------------------------------------------------
-- 1. SPU表（商品主表）
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_spu (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT 'SPU ID',
    name VARCHAR(255) NOT NULL COMMENT '商品名称',
    keyword VARCHAR(500) DEFAULT NULL COMMENT '关键词（逗号分隔）',
    introduction TEXT DEFAULT NULL COMMENT '商品简介',
    description TEXT DEFAULT NULL COMMENT '详细描述',
    category_id INT DEFAULT NULL COMMENT '分类ID',
    brand_id INT DEFAULT NULL COMMENT '品牌ID',
    price DECIMAL(10, 2) DEFAULT NULL COMMENT '参考价格',
    stock INT DEFAULT 0 COMMENT '库存数量',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_name (name),
    INDEX idx_category (category_id),
    INDEX idx_brand (brand_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品SPU表';

-- ----------------------------------------------------------
-- 2. SKU表（商品规格表）
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_sku (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT 'SKU ID',
    spu_id INT NOT NULL COMMENT '关联的SPU ID',
    properties JSON DEFAULT NULL COMMENT '规格属性（JSON格式）',
    price DECIMAL(10, 2) NOT NULL DEFAULT 0 COMMENT 'SKU价格',
    stock INT NOT NULL DEFAULT 0 COMMENT '库存数量',
    bar_code VARCHAR(64) DEFAULT NULL COMMENT '条形码',
    pic_url VARCHAR(500) DEFAULT NULL COMMENT '商品图片URL',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_spu_id (spu_id),
    INDEX idx_bar_code (bar_code),
    FOREIGN KEY (spu_id) REFERENCES product_spu(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品SKU表';

-- ----------------------------------------------------------
-- 3. Alias表（商品别名表）
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_alias (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '别名ID',
    spu_id INT NOT NULL COMMENT '关联的SPU ID',
    alias_name VARCHAR(255) NOT NULL COMMENT '别名原文',
    alias_normalized VARCHAR(255) NOT NULL COMMENT '标准化后的别名',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_alias_normalized (alias_normalized),
    INDEX idx_spu_id (spu_id),
    FOREIGN KEY (spu_id) REFERENCES product_spu(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品别名表';

-- ----------------------------------------------------------
-- 4. 测试数据
-- ----------------------------------------------------------
-- 插入测试SPU数据
INSERT INTO product_spu (id, name, keyword, introduction, description, category_id, brand_id, price, stock) VALUES
(1, 'iPhone 16 Pro', '苹果,iPhone,旗舰手机,5G', 'Apple最新旗舰智能手机', '搭载A18 Pro芯片，支持5G网络，配备专业级摄像头系统', 1, 1, 7999.00, 500),
(2, 'MacBook Pro 16寸', '苹果,笔记本,MacBook,办公', 'Apple专业级笔记本电脑', 'M3 Max芯片，36GB内存，1TB SSD', 2, 1, 24999.00, 200),
(3, 'AirPods Pro', '苹果,耳机,蓝牙耳机,降噪', 'Apple主动降噪无线耳机', '支持主动降噪，空间音频，MagSafe充电盒', 3, 1, 1899.00, 1000);

-- 插入测试SKU数据
INSERT INTO product_sku (id, spu_id, properties, price, stock, bar_code, pic_url) VALUES
(1, 1, '{"color":"黑色钛金属","storage":"256GB"}', 7999.00, 200, '6901234567890', 'https://example.com/iphone16-pro-black.jpg'),
(2, 1, '{"color":"黑色钛金属","storage":"512GB"}', 8999.00, 150, '6901234567891', 'https://example.com/iphone16-pro-black-512.jpg'),
(3, 1, '{"color":"白色钛金属","storage":"256GB"}', 7999.00, 150, '6901234567892', 'https://example.com/iphone16-pro-white.jpg'),
(4, 2, '{"color":"银色","storage":"36GB/1TB"}', 24999.00, 50, 'ABC1234567890', 'https://example.com/macbook-pro-16.jpg'),
(5, 3, '{"color":"白色","version":"第二代"}', 1899.00, 500, 'ABC1234567891', 'https://example.com/airpods-pro.jpg');

-- 插入测试别名数据
INSERT INTO product_alias (spu_id, alias_name, alias_normalized) VALUES
(1, '苹果手机', '苹果手机'),
(1, 'iPhone', 'iphone'),
(1, '水果机', '水果机'),
(2, 'MacBook', 'macbook'),
(2, '苹果笔记本', '苹果笔记本'),
(3, 'AirPods', 'airpods'),
(3, '苹果耳机', '苹果耳机');

-- ----------------------------------------------------------
-- 5. ETL字典构建（从数据库拉取数据构建JSON）
-- ----------------------------------------------------------
-- 运行命令：python -m app.etl --output data/product_dict.json --pretty
-- ----------------------------------------------------------

-- ----------------------------------------------------------
-- 6. 维护存储过程（可选）
-- ----------------------------------------------------------
-- 别名同步存储过程：当SPU名称更新时，自动同步别名
DELIMITER //
CREATE PROCEDURE IF NOT EXISTS sync_spu_aliases(IN p_spu_id INT)
BEGIN
    DECLARE v_name VARCHAR(255);
    SELECT name INTO v_name FROM product_spu WHERE id = p_spu_id;
    INSERT INTO product_alias (spu_id, alias_name, alias_normalized)
    VALUES (p_spu_id, v_name, normalize_text(v_name))
    ON DUPLICATE KEY UPDATE alias_name = v_name;
END //
DELIMITER ;