package ssadump;
// tools/servercheck/ssadump/Dump.java — dumps one builtin worldgen registry of an
// obfuscated 1.16.2-1.17.1 jar with its own Codec (JsonOps, references inline).
//   java -Dpre=<SharedConstants> -cp <cp>:. ssadump.Dump <Bootstrap> <BuiltinRegistries> <field> <elementClass> <out.json> <ResourceLocation>
import com.mojang.serialization.*;
import com.google.gson.*;
import java.lang.reflect.*;
import java.nio.file.*;
public class Dump {
  public static void main(String[] a) throws Exception {
    String boot=a[0], regHolder=a[1], regField=a[2], poolCls=a[3], out=a[4];
    String pre = System.getProperty("pre"); if (pre != null) Class.forName(pre).getMethod("a").invoke(null);
    Class.forName(boot).getMethod("a").invoke(null);
    Object reg = Class.forName(regHolder).getField(regField).get(null);
    Class<?> pc = Class.forName(poolCls);
    Codec codec = null;
    for (Field f : pc.getFields()) if (Modifier.isStatic(f.getModifiers()) && Codec.class.isAssignableFrom(f.getType())) { codec = (Codec) f.get(null); break; }
    JsonObject o = new JsonObject();
    Method getKey = null;
    for (Method m : reg.getClass().getMethods()) if (m.getParameterCount()==1 && m.getReturnType().getSimpleName().equals(a.length > 5 ? a[5] : "vk")) getKey = m;
    int n = 0;
    for (Object p : (Iterable<?>) reg) {
      DataResult r = codec.encodeStart(JsonOps.INSTANCE, p);
      Object res = r.result().orElse(null);
      String key = getKey != null ? String.valueOf(getKey.invoke(reg, p)) : ("e" + n);
      if (res == null) { System.err.println("fail " + key + " " + r.error()); continue; }
      o.add(key, (JsonElement) res); n++;
    }
    Files.write(Paths.get(out), new GsonBuilder().setPrettyPrinting().create().toJson(o).getBytes());
    System.out.println("dumped " + n);
  }
}
