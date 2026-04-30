-- =====================================================
-- 增量摄取功能数据库迁移脚本
-- 文件: upgrade_incremental_ingest.sql
-- 用途: 为 documents 表添加增量摄取所需字段
-- 目标: MySQL 8.x
-- =====================================================

-- =====================================================
-- 使用说明:
-- 1. 在执行前备份数据库
-- 2. 确保有足够的权限修改表结构
-- 3. 执行命令: mysql -u your_user -p lumapepe < upgrade_incremental_ingest.sql
-- 4. 验证迁移: DESCRIBE documents;
-- =====================================================

USE lumapepe;

-- =====================================================
-- 第一步: 添加 source_path 列
-- 用于存储原始文件/文件夹路径，便于增量检测
-- =====================================================

-- 使用存储过程确保列不存在时才添加（可安全重复执行）
DELIMITER //

DROP PROCEDURE IF EXISTS add_source_path_column//

CREATE PROCEDURE add_source_path_column()
BEGIN
    -- 检查列是否已存在
    IF NOT EXISTS (
        SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'documents'
        AND COLUMN_NAME = 'source_path'
    ) THEN
        -- 在 file_path 列之后添加 source_path
        ALTER TABLE documents
        ADD COLUMN source_path VARCHAR(512) AFTER file_path;

        SELECT 'Column source_path added successfully.' AS result;
    ELSE
        SELECT 'Column source_path already exists, skipping.' AS result;
    END IF;
END//

DELIMITER ;

CALL add_source_path_column();
DROP PROCEDURE IF EXISTS add_source_path_column;

-- =====================================================
-- 第二步: 添加 file_mtime 列
-- 用于存储文件最后修改时间，支持增量检测
-- DOUBLE 类型用于存储 Unix 时间戳（秒级精度）
-- =====================================================

DELIMITER //

DROP PROCEDURE IF EXISTS add_file_mtime_column//

CREATE PROCEDURE add_file_mtime_column()
BEGIN
    -- 检查列是否已存在
    IF NOT EXISTS (
        SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'documents'
        AND COLUMN_NAME = 'file_mtime'
    ) THEN
        -- 在 source_path 列之后添加 file_mtime
        ALTER TABLE documents
        ADD COLUMN file_mtime DOUBLE AFTER source_path;

        SELECT 'Column file_mtime added successfully.' AS result;
    ELSE
        SELECT 'Column file_mtime already exists, skipping.' AS result;
    END IF;
END//

DELIMITER ;

CALL add_file_mtime_column();
DROP PROCEDURE IF EXISTS add_file_mtime_column;

-- =====================================================
-- 第三步: 创建 source_path 索引
-- 加速按源路径查询和去重检查
-- =====================================================

-- 检查索引是否已存在
DELIMITER //

DROP PROCEDURE IF EXISTS add_source_path_index//

CREATE PROCEDURE add_source_path_index()
BEGIN
    -- MySQL 8.x 使用 INFORMATION_SCHEMA.STATISTICS 检查索引
    IF NOT EXISTS (
        SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'documents'
        AND INDEX_NAME = 'idx_source_path'
    ) THEN
        -- 创建索引
        ALTER TABLE documents
        ADD INDEX idx_source_path (source_path);

        SELECT 'Index idx_source_path created successfully.' AS result;
    ELSE
        SELECT 'Index idx_source_path already exists, skipping.' AS result;
    END IF;
END//

DELIMITER ;

CALL add_source_path_index();
DROP PROCEDURE IF EXISTS add_source_path_index;

-- =====================================================
-- 第四步: 验证迁移结果
-- =====================================================

-- 显示 documents 表结构
SELECT 'Migration completed. Current table structure:' AS status;
DESCRIBE documents;

-- 显示所有索引
SELECT 'Indexes on documents table:' AS status;
SHOW INDEX FROM documents;
