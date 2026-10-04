package net.fabricmc.api; import java.lang.annotation.*; @Retention(RetentionPolicy.CLASS) public @interface Environment { EnvType value(); }
