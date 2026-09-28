package com.miniapp.wanhe;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration;

/**
 * 万合物管小程序后端接口启动类。
 *
 * <p>当前数据库环境未就绪，暂时排除 DataSource 自动装配，保证无 MySQL 环境也能直接启动；
 * 数据库配置完成后，移除 exclude 并放开 application.yml 中的 datasource 配置即可。</p>
 */
@SpringBootApplication(exclude = DataSourceAutoConfiguration.class)
public class WanheServiceApiApplication {

    public static void main(String[] args) {
        SpringApplication.run(WanheServiceApiApplication.class, args);
    }
}
