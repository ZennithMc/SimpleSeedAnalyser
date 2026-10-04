import java.io.*; import java.util.*;
import net.minecraft.util.registry.Registry; import net.minecraft.world.biome.Biome;
import net.minecraft.world.gen.GenerationStep; import net.minecraft.world.gen.feature.*;
import net.minecraft.world.gen.decorator.*;
import nl.jellejurre.seedchecker.*;
public class Lakes {
  public static void main(String[] a) throws Exception {
    PrintStream out=System.out; System.setOut(new PrintStream(new OutputStream(){public void write(int b){}}));
    new SeedChecker(0L,TargetState.NO_STRUCTURES,SeedCheckerDimension.OVERWORLD);
    for(Biome b: Registry.BIOME){
      StringBuilder sb=new StringBuilder(Registry.BIOME.getRawId(b)+" "+Registry.BIOME.getId(b).getPath()+" :");
      for(ConfiguredFeature<?,?> cf: b.getFeaturesForStep(GenerationStep.Feature.LAKES)){
        if(cf.config instanceof DecoratedFeatureConfig){ DecoratedFeatureConfig d=(DecoratedFeatureConfig)cf.config;
          String dec=Registry.DECORATOR.getId(d.decorator.decorator).getPath();
          String ch=(d.decorator.config instanceof ChanceDecoratorConfig)? ""+((ChanceDecoratorConfig)d.decorator.config).chance : "?";
          String st=(d.feature.config instanceof SingleStateFeatureConfig)? ((SingleStateFeatureConfig)d.feature.config).state.getBlock().toString():"?";
          sb.append(" [").append(Registry.FEATURE.getId(d.feature.feature).getPath()).append(' ').append(dec).append(' ').append(ch).append(' ').append(st).append(']');
        } else sb.append(" [").append(Registry.FEATURE.getId(cf.feature).getPath()).append(']');
      }
      out.println(sb);
    }
  }
}
