# ============================================================
# 万合物管小程序后端接口 - 运行镜像（wanhe-service-api）
#
# 与 Jenkins 配合的使用方式：Maven 构建在 Jenkins 的 Maven 阶段完成，
# 本 Dockerfile 只负责把构建产物打包成轻量运行镜像。
#
# 构建前提：
#   1. 仓库根目录执行 Maven 打包：
#        mvn clean package -pl wanhe-service-api -am
#   2. 构建镜像（注意：构建上下文是子模块目录）：
#        docker build -t wanhe-service-api:1.0.0 wanhe-service-api
#
# 运行示例：
#   docker run -d --name wanhe-service-api -p 8082:8082 \
#     -e JAVA_TOOL_OPTIONS="-Xms256m -Xmx512m" \
#     wanhe-service-api:1.0.0
# ============================================================
FROM eclipse-temurin:21-jre

# 容器时区
ENV TZ=Asia/Shanghai

WORKDIR /app

# 非 root 用户运行，符合容器安全基线要求
RUN useradd -r -u 1001 -s /sbin/nologin appuser

# 默认打包 target 下的 Maven 构建产物，可用 --build-arg JAR_FILE=xxx.jar 覆盖
ARG JAR_FILE=target/*.jar
COPY ${JAR_FILE} app.jar

USER appuser

EXPOSE 8082

# exec 形式启动：java 进程即 PID 1，可正确接收 SIGTERM 实现优雅停机。
# JVM 参数通过环境变量注入：-e JAVA_TOOL_OPTIONS="-Xms256m -Xmx512m"
# 应用启动参数直接追加在 docker run 命令末尾（如 --spring.profiles.active=prod）
ENTRYPOINT ["java", "-Duser.timezone=Asia/Shanghai", "-jar", "/app/app.jar"]
