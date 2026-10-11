-- =====================================================================
-- 富贵记账 · 数据库初始化脚本（MySQL 5.7+ / 8.x，InnoDB + utf8mb4）
-- 用法：
--   mysql -h<host> -P<port> -u<user> -p < sql/init.sql
-- 说明：
--   1) 库名默认 prosperity_bookkeeping，如需改名请同步修改：
--      本文件的两处库名 + 云托管环境变量 MYSQL_DATABASE
--   2) 重复执行不会丢数据（CREATE TABLE IF NOT EXISTS）
-- =====================================================================

CREATE DATABASE IF NOT EXISTS `prosperity_bookkeeping`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `prosperity_bookkeeping`;

-- ---------------------------------------------------------------------
-- 用户表：一个微信用户一行，openid 唯一
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `users` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户ID',
  `openid`     VARCHAR(64)  NOT NULL COMMENT '微信小程序 openid',
  `union_id`   VARCHAR(64)  NULL     COMMENT '微信开放平台 unionid（可空）',
  `nickname`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '昵称（用户手动填写）',
  `avatar_url` VARCHAR(512) NOT NULL DEFAULT '' COMMENT '头像地址',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_openid` (`openid`),
  KEY `idx_union_id` (`union_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- ---------------------------------------------------------------------
-- 房间表：room_no 为展示/邀请用的 6 位数字房号
-- status: 0=等待中(waiting) 1=游戏中(playing) 2=已结束(finished)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `rooms` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '房间ID',
  `room_no`     CHAR(6)      NOT NULL COMMENT '6位数字房号',
  `owner_id`    BIGINT UNSIGNED NOT NULL COMMENT '房主用户ID（users.id）',
  `name`        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '房间名称（预留）',
  `max_players` TINYINT UNSIGNED NOT NULL DEFAULT 8 COMMENT '最大人数',
  `status`      TINYINT      NOT NULL DEFAULT 0 COMMENT '状态：0等待中 1游戏中 2已结束',
  `finished_at` DATETIME     NULL COMMENT '结束时间',
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_room_no` (`room_no`),
  KEY `idx_owner_status` (`owner_id`, `status`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='房间表';

-- ---------------------------------------------------------------------
-- 房间成员表：昵称/头像在此冗余一份（入房时快照，房间内可单独修改）
-- role: 0=房主 1=普通成员
-- score: 实时累计积分（每笔流水在事务内增减）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `room_members` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '成员记录ID',
  `room_id`    BIGINT UNSIGNED NOT NULL COMMENT '房间ID',
  `user_id`    BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
  `openid`     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT 'openid 快照（便于前端匹配）',
  `nickname`   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '房间内昵称',
  `avatar_url` VARCHAR(512) NOT NULL DEFAULT '' COMMENT '房间内头像',
  `role`       TINYINT      NOT NULL DEFAULT 1 COMMENT '角色：0房主 1成员',
  `score`      INT          NOT NULL DEFAULT 0 COMMENT '累计积分（正=收入 负=支出）',
  `joined_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '加入时间',
  `updated_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_room_user` (`room_id`, `user_id`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_room_score` (`room_id`, `score`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='房间成员表';

-- ---------------------------------------------------------------------
-- 积分流水表：每笔转账一行（付款人 -> 收款人）
-- amount 恒为正整数；付款方扣分、收款方加分，事务内完成
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `score_records` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '流水ID',
  `room_id`        BIGINT UNSIGNED NOT NULL COMMENT '房间ID',
  `from_member_id` BIGINT UNSIGNED NOT NULL COMMENT '付款成员ID（room_members.id）',
  `to_member_id`   BIGINT UNSIGNED NOT NULL COMMENT '收款成员ID（room_members.id）',
  `from_openid`    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '付款人 openid 快照',
  `to_openid`      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '收款人 openid 快照',
  `amount`         INT          NOT NULL COMMENT '金额（正整数）',
  `remark`         VARCHAR(128) NOT NULL DEFAULT '' COMMENT '备注（预留）',
  `operator_id`    BIGINT UNSIGNED NOT NULL COMMENT '操作人用户ID',
  `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_room_id` (`room_id`, `id`),
  KEY `idx_from_member` (`from_member_id`),
  KEY `idx_to_member` (`to_member_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='积分流水表';
