# Fmod Bank Tools 简明教程

> 这是给 FH6 Radio Tool 配套使用的超简版说明。  
> 更详细内容请看 Fmod Bank Tools 原仓库。

仓库地址：

```text
https://github.com/Wouldubeinta/Fmod-Bank-Tools.git
```

## 1. Fmod Bank Tools 是干嘛的

它主要做两件事：

```text
Extract：把 .bank 拆出 wav 和 txt
Rebuild：用 wav 和 txt 重新打包 .bank
```

FH6 Radio Tool 不直接生成 bank，所以仍然需要它。

## 2. 下载

优先去仓库页面找 Releases。  
如果有已打包版本，下载后解压即可。

如果没有现成包，就需要自己编译源码。  
普通用户建议先看 B 站教程或找别人整理好的构建说明。

## 3. Extract 原 bank

1. 打开 Fmod Bank Tools；
2. 设置 bank 输入目录；
3. 把原游戏 bank 放进去；
4. 点击 Extract；
5. 等待完成。

完成后，你会看到类似：

```text
R4_Tracks_CU1.assets[0].txt
R4_Tracks_CU1.assets[0]/
  sound_0.wav
  sound_1.wav
```

这个目录就是后面要导入 FH6 Radio Tool 的 Extract wav 输出目录。

## 4. 导入到 FH6 Radio Tool

在 FH6 Radio Tool 里点击：

```text
② 导入 Extract
```

选择刚刚 Fmod Bank Tools 生成 wav 和 txt 的那个目录。

不要选错：

```text
不要选 bank 目录
不要选 build 目录
不要选单个 sound_x.wav 文件夹
```

## 5. Rebuild 新 bank

FH6 Radio Tool 最终生成后，会得到：

```text
output/fmod_ready_wav/
```

回到 Fmod Bank Tools，把：

```text
Wav Output Directory
```

设置为：

```text
output/fmod_ready_wav
```

然后点击 Rebuild。

重构完成后，到 Fmod Bank Tools 的 build 输出目录拿新 bank。

## 6. 最容易错的地方

```text
把原 bank 当成新 bank 放回游戏
导入了错误电台的 Extract 目录
Wav Output Directory 没有指向 fmod_ready_wav
替换了错误的 bank 文件
游戏没重启
```

遇到问题时，优先检查这些。
