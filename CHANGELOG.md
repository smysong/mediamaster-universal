# 更新日志

所有显著的变更都会记录在此文档中。此项目遵循 [语义化版本控制](https://semver.org/spec/v2.0.0.html)。

## [1.0.0] - 2024-12-26

### 说明
- 基于[MediaMaster V1.7.0](https://github.com/smysong/mediamaster/releases/tag/1.7.0)版本代码构建。

### 增加
- 无。

### 修复
- 无。

### 修改
- 修改获取TMDB ID的逻辑，优化代码结构和程序运行逻辑。

### 移除
- 移除了对EmbyServer、TinyMediaManager API的依赖，让使用飞牛、绿联、极空间等NAS自带的影视媒体库系统可以使用。
- 移除了使用Bark发送通知的功能、移除了对NFO文件中的演职人员信息汉化功能。
- 移除了配置文件中的上述功能的相关配置项。

[1.0.0]: https://github.com/smysong/mediamaster/releases/tag/1.0.0
